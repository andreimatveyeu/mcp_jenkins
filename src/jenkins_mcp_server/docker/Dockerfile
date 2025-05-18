FROM python:3.12-slim

WORKDIR /app

COPY src/jenkins_mcp_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt pytest requests

# Create the target directory structure within /app
# and copy the application code into /app/src/jenkins_mcp_server/
# This ensures chat_interface.py is found at /app/src/jenkins_mcp_server/chat_interface.py
# and main.py will be at /app/src/jenkins_mcp_server/main.py
RUN mkdir -p /app/src/jenkins_mcp_server
COPY src/jenkins_mcp_server/ /app/src/jenkins_mcp_server/

ENV JENKINS_URL=${JENKINS_URL}
ENV JENKINS_USER=${JENKINS_USER}
ENV JENKINS_API_TOKEN=${JENKINS_API_TOKEN}

# main.py is the actual Flask application, now located in a subdirectory.
# The WORKDIR is /app, so the path is relative to that.
ENTRYPOINT ["python", "src/jenkins_mcp_server/main.py"]
