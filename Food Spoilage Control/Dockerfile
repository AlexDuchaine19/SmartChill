# Use Python 3.8 on Alpine as the base image
FROM python:3.8-alpine

# Set the working directory inside the container
WORKDIR /app

# Copy all project files (including requirements.txt) into the container
COPY . .

# Install the dependencies from requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Set the command that will be executed when the container starts
CMD ["python3", "SpoilageControl.py"]