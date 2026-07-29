# Use a lightweight official Python runtime
FROM python:3.11-slim

# Prevent Python from writing bytecode (.pyc) and keep stdout unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Copy application files into the container
COPY . .

# Cloud Run injects the PORT environment variable (default 8080)
ENV PORT=8080
EXPOSE 8080

# Run server.py using Python
CMD ["python", "server.py"]