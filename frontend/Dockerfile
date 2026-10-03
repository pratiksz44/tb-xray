# syntax=docker/dockerfile:1.7
# Unprivileged nginx: runs as a non-root user and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.27-alpine

ENV BACKEND_URL=http://backend:8000 \
    BACKEND_HOST=backend

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY src/ /usr/share/nginx/html/

EXPOSE 8080
