FROM python:3.12-slim

WORKDIR /app

COPY src/mcp_jenkins/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt pytest requests

# Create the target directory structure within /app
# and copy the application code into /app/src/mcp_jenkins/
# This ensures client.py is found at /app/src/mcp_jenkins/client.py
# and main.py will be at /app/src/mcp_jenkins/main.py
RUN mkdir -p /app/src/mcp_jenkins
COPY src/mcp_jenkins/ /app/src/mcp_jenkins/

ENV JENKINS_URL=${JENKINS_URL}
ENV JENKINS_USER=${JENKINS_USER}
ENV JENKINS_API_TOKEN=${JENKINS_API_TOKEN}

# main.py is the actual Flask application, now located in a subdirectory.
# The WORKDIR is /app, so the path is relative to that.
ENTRYPOINT ["python", "src/mcp_jenkins/main.py"]
