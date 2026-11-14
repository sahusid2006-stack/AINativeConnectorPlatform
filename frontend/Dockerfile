FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci && chown -R node:node /app
COPY --chown=node:node frontend ./
USER node
CMD ["npm", "run", "dev"]
