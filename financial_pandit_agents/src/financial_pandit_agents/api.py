"""HTTP interface for Financial Pandit; run with Uvicorn."""

import asyncio
import json
import logging
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from financial_pandit_agents.main import run_assignment

logger = logging.getLogger(__name__)

app = FastAPI(title="Financial Pandit API")
MODEL_ID = "financial-pandit"


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class ChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant"]
    content: str | list[TextPart]

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(part.text for part in self.content)


class ChatCompletionRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    n: Literal[1] = 1


def chat_error(message: str, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "message": message,
        "type": "server_error" if status >= 500 else "invalid_request_error",
        "param": None,
        "code": code,
    }})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/"):
        return chat_error("Invalid chat request. Supply text messages and n=1.",
                          400, "invalid_request")
    return await request_validation_exception_handler(request, exc)


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{
        "id": MODEL_ID, "object": "model", "created": 0,
        "owned_by": "financial-pandit",
    }]}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Adapt OpenAI text chat requests to the existing financial crew."""
    if request.model != MODEL_ID:
        return chat_error(f"Unknown model: {request.model}", 404, "model_not_found")
    # LibreChat sends prior turns on every request; no server session is needed.
    if request.messages[-1].role != "user" or not request.messages[-1].text().strip():
        return chat_error("The final message must be a nonblank user question.",
                          400, "invalid_request")
    question = request.messages[-1].text().strip()
    context = json.dumps([
        {"role": message.role, "content": message.text()}
        for message in request.messages[:-1]
    ], ensure_ascii=False) if len(request.messages) > 1 else ""
    metadata = {
        "id": f"chatcmpl-{uuid4().hex}",
        "created": int(time.time()),
        "model": MODEL_ID,
    }

    if not request.stream:
        try:
            answer = await run_in_threadpool(
                run_assignment, question=question, session_context=context,
            )
        except Exception:
            logger.exception("Chat completion failed")
            return chat_error("Unable to complete the financial analysis.",
                              500, "internal_error")
        return {**metadata, "object": "chat.completion", "choices": [{
            "index": 0, "message": {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }]}

    async def events():
        def chunk(delta: dict, finish_reason=None) -> str:
            payload = {**metadata, "object": "chat.completion.chunk", "choices": [{
                "index": 0, "delta": delta, "finish_reason": finish_reason,
            }]}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        task = asyncio.create_task(run_in_threadpool(
            run_assignment, question=question, session_context=context,
        ))
        try:
            # Keep the HTTP stream alive while the synchronous crew works.
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=10)
                if not done:
                    yield ": keep-alive\n\n"
            answer = await task
            # The crew returns a complete answer, so emit one content delta.
            yield chunk({"content": answer})
            yield chunk({}, "stop")
        except Exception:
            logger.exception("Streaming chat completion failed")
            yield 'data: {"error":{"message":"Unable to complete the financial analysis.","type":"server_error","code":"internal_error"}}\n\n'
        finally:
            # Cancelling the await cannot interrupt an already running crew thread.
            if not task.done():
                task.cancel()
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    session_context: str = ""

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question must not be blank.")
        return value


class AnswerResponse(BaseModel):
    answer: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=AnswerResponse)
def ask(request: QuestionRequest) -> AnswerResponse:
    """Run the synchronous crew in FastAPI's thread pool and return its answer."""
    try:
        answer = run_assignment(
            question=request.question,
            session_context=request.session_context,
        )
        return AnswerResponse(answer=answer)
    except Exception:
        logger.exception("Financial analysis failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to complete the financial analysis.",
        ) from None
