FROM python:3.12-slim

WORKDIR /app/mcp_jenkins

COPY src/mcp_jenkins/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest requests

# Copy the application code into the WORKDIR
COPY src/mcp_jenkins/ .

ENV JENKINS_URL=${JENKINS_URL}
ENV JENKINS_USER=${JENKINS_USER}
ENV JENKINS_API_TOKEN=${JENKINS_API_TOKEN}

ENTRYPOINT ["python", "server.py"]
