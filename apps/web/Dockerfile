FROM node:22-alpine AS build
ARG NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
WORKDIR /web
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build
FROM nginx:1.27-alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
