# Stage 2 Service Execution Guide

## Build and Run

To build the standalone container:
```bash
docker build -t pocketful-stage-2 .
```

To run the container:
```bash
docker run -p 8080:8080 -e PORT=8080 pocketful-stage-2
```

The service will be accessible at `http://localhost:8080`.
The health endpoint is at `GET http://localhost:8080/health`.
State reset is at `POST http://localhost:8080/_test/reset`.
