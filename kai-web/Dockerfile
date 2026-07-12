# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
RUN npm run build

# Serve stage
FROM nginx:alpine
RUN apk add --no-cache apache2-utils nginx-module-njs
RUN sed -i '1i env KAI_WORKER_AUTH_B64;\nload_module /usr/lib/nginx/modules/ngx_http_js_module.so;' /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY worker_auth.js /etc/nginx/njs/worker_auth.js
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
EXPOSE 80
