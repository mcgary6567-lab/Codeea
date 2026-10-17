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
RUN cd /app && npm ci --omit=dev -w server && npm cache clean --force \
  && mkdir -p /data && chown -R node:node /data
# the app never needs root
USER node
VOLUME /data
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:4000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "--disable-warning=ExperimentalWarning", "dist/index.js"]
