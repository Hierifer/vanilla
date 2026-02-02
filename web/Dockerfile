# Use Node 20 to satisfy Nuxt engine requirements
FROM node:20-alpine AS base
WORKDIR /app

FROM base AS deps
COPY package*.json ./
RUN npm install

FROM deps AS build
COPY . .
RUN npm run build

FROM base AS runtime
ENV NODE_ENV=production
ENV NITRO_PORT=3000
ENV NUXT_HOST=0.0.0.0
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/.output ./.output
COPY package.json ./
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
