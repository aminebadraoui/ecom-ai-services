# AI-Powered Content Extraction Service

This FastAPI application provides AI-powered endpoints for extracting structured information from product images and sales pages.

## Features

- `/extract-ad-concept` - Analyzes product images and extracts structured information about layout, visual elements, and design principles
- `/extract-sales-page` - Extracts key marketing information from sales pages for creating effective ads
- `/generate-ad-recipe` - Combines ad concept and sales page data to generate a comprehensive ad creation recipe
- Background task processing with Celery and Redis
- Server-Sent Events (SSE) for real-time task updates
- GPT-4o powered analysis using pydantic-ai
- Supabase integration for persistent storage

## Local Setup

### Prerequisites

- Python 3.9+
- Redis server
- OpenAI API key

### Installation

1. Create a virtual environment:
   ```
   ./setup_venv.sh
   ```
   Or manually:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Set up environment variables by creating a `.env` file in the project root with the following content:
   ```
   OPENAI_API_KEY=your_openai_api_key
   REDIS_HOST=localhost
   REDIS_PORT=6379
   REDIS_DB=0
   REDIS_PASSWORD=your_redis_password  # Optional
   SUPABASE_URL=your_supabase_url
   SUPABASE_KEY=your_supabase_key
   ```

### Running the application locally

1. Start Redis server:
   ```
   redis-server
   ```

2. Start Celery worker:
   ```
   python worker.py
   ```

3. Run the FastAPI application:
   ```
   python main.py
   ```

The server will start on `http://localhost:8000`.

## Docker Setup

You can also run the application using Docker and Docker Compose:

```
docker-compose up -d
```

This will start the FastAPI application, Celery worker, and Redis server.

## Deployment on Coolify

### Prerequisites

1. A GitHub repository with your code
2. A Coolify instance up and running
3. Basic knowledge of Git and Coolify

### Steps

1. Push your code to GitHub:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/your-username/your-repo.git
   git push -u origin main
   ```

2. Log in to your Coolify dashboard

3. Add a new resource in Coolify:
   - Select "Docker Compose" as the resource type
   - Connect to your GitHub repository
   - Configure the build settings:
     - Build Method: Docker Compose
     - Docker Compose File: docker-compose.yml
     - Dockerfile: Dockerfile
   
4. Configure environment variables in Coolify:
   - OPENAI_API_KEY (required)
   - REDIS_HOST (will be 'redis' if using the docker-compose.yml)
   - REDIS_PORT (typically 6379)
   - REDIS_DB (typically 0)
   - REDIS_PASSWORD (if needed)
   - SUPABASE_URL (required for database features)
   - SUPABASE_KEY (required for database features)

5. Deploy your application by clicking the "Deploy" button

6. Access your application at the provided Coolify URL

## API Usage

### Extract Ad Concept from an Image

```bash
curl -X POST "http://localhost:8000/api/v1/extract-ad-concept" \
     -H "Content-Type: application/json" \
     -d '{"image_url": "https://example.com/product-image.jpg"}'
```

### Extract Sales Page Information

```bash
curl -X POST "http://localhost:8000/api/v1/extract-sales-page" \
     -H "Content-Type: application/json" \
     -d '{"page_url": "https://example.com/sales-page"}'
```

### Generate Ad Recipe

```bash
curl -X POST "http://localhost:8000/api/v1/generate-ad-recipe" \
     -H "Content-Type: application/json" \
     -d '{
         "ad_archive_id": "123456789",
         "image_url": "https://example.com/product-image.jpg",
         "sales_url": "https://example.com/sales-page"
     }'
```

### Check Task Status

```bash
curl "http://localhost:8000/api/v1/tasks/{task_id}"
```

Replace `{task_id}` with the ID received from the extraction endpoint.

### Real-time Task Updates with Server-Sent Events

To receive real-time updates on task progress, use the SSE endpoint:

```
http://localhost:8000/api/v1/tasks/{task_id}/stream
```

You can consume this in JavaScript with:

```javascript
const eventSource = new EventSource(`/api/v1/tasks/${taskId}/stream`);

eventSource.addEventListener('update', (event) => {
  const data = JSON.parse(event.data);
  console.log('Task update:', data);
  
  if (data.status === 'completed' || data.status === 'failed') {
    eventSource.close();
  }
});

eventSource.addEventListener('timeout', () => {
  console.log('Task processing timed out');
  eventSource.close();
});
```

## API Documentation

Once the application is running, you can access the API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc` 