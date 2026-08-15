FROM node:22.18.0-alpine AS build

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY index.html eslint.config.js tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts vitest.config.ts playwright.config.ts ./
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27.4-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 8080
