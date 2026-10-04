FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY server/package.json server/
COPY web/package.json web/
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app/server
ENV NODE_ENV=production PORT=4000 DB_PATH=/data/pumpai.db WEB_DIST=/app/web/dist
COPY --from=build /app/package.json /app/package-lock.json /app/
COPY --from=build /app/server/package.json ./
COPY --from=build /app/server/dist ./dist
COPY --from=build /app/web/dist /app/web/dist
RUN cd /app && npm ci --omit=dev -w server
VOLUME /data
EXPOSE 4000
CMD ["node", "--disable-warning=ExperimentalWarning", "dist/index.js"]
