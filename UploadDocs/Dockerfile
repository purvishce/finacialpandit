FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="Financial Pandit UploadDocs" \
      org.opencontainers.image.description="Static statement upload form"

# Replace the default landing page and copy only the files needed by the SPA.
RUN rm -f /usr/share/nginx/html/index.html
COPY index.html styles.css app.js /usr/share/nginx/html/

# The image is intended to be fronted by HTTPS at the platform edge.
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
