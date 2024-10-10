FROM python:3.9-slim

# Install required Python packages
COPY requirements.txt /action/requirements.txt
RUN pip install --no-cache-dir -r /action/requirements.txt

# Copy the action script
COPY spell_check.py /action/spell_check.py

# Set the entrypoint to run the Python script
ENTRYPOINT ["python", "/action/spell_check.py"]
