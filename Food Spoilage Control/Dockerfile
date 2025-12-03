# Use Python 3.8 on Alpine
FROM python:3.8-alpine

# Set the generic working directory
WORKDIR /app

# 1. Copy the Shared/Common folder into the container
# This is crucial so the service can import BaseService
COPY Common ./Common

# 2. Copy the specific service folder
# We use quotes because the folder name has spaces
COPY ["Food Spoilage Control", "./FoodSpoilageControl/"]

# 3. Set working directory to the specific service folder
WORKDIR /app/FoodSpoilageControl

# 4. Install dependencies
# We assume requirements.txt is inside the "Food Spoilage Control" folder
RUN pip3 install --no-cache-dir -r requirements.txt

# 5. Run the script
CMD ["python3", "SpoilageControl.py"]