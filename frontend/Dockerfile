FROM node:20-alpine

WORKDIR /app

# Copy package.json only (not lock file, ensure clean install)
COPY package.json ./
RUN npm install

# Copy rest of source
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
