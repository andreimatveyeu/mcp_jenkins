FROM python:3.12-slim

WORKDIR /app

COPY src/jenkins_mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/jenkins_mcp_server/main.py src/jenkins_mcp_server/
COPY src/jenkins_mcp_server/chat_interface.py src/jenkins_mcp_server/

ENV JENKINS_URL=${JENKINS_URL}
ENV JENKINS_USER=${JENKINS_USER}
ENV JENKINS_API_TOKEN=${JENKINS_API_TOKEN}

CMD ["python", "main.py"]
