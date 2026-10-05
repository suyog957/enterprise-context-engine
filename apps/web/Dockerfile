FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM nginx:1.27-alpine
# The official image renders /etc/nginx/templates/*.template with envsubst at start-up.
COPY apps/web/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=http://api:8000/ \
    NGINX_ENVSUBST_FILTER=API_UPSTREAM
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
