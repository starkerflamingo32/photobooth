# Test runner: Playwright with its browsers, same version as package.json.
FROM mcr.microsoft.com/playwright:v1.49.1-noble
WORKDIR /work
COPY package.json ./
RUN npm install --no-audit --no-fund
ENTRYPOINT ["npx", "playwright", "test"]
