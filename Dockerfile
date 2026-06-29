FROM python:3.11-slim

# Create a non-root user (Hugging Face Spaces requires this for security)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Copy the entire repository into the container
COPY --chown=user . /app

# Install backend dependencies
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 7860 (Hugging Face Spaces default)
EXPOSE 7860

# Run the FastAPI server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
