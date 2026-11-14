FROM node:22-bookworm-slim
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci && chown -R node:node /app
COPY --chown=node:node frontend ./
USER node
CMD ["npm", "run", "dev"]
