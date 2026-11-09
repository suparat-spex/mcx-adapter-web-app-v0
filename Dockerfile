# MCX Adapter - adapter + web console image.
#   docker build -t mcx-adapter .
#   docker run -p 8810:8810 -p 8811:8811 -v $PWD/plant:/config mcx-adapter
# Console: http://<host>:8810 (first visit creates the admin). MCP endpoint: http://<host>:8811/mcp/<profile>
# Set endpoint.host to 0.0.0.0 in /config/plant.mcx so clients outside the container reach the MCP port.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres,mysql]"
EXPOSE 8810 8811
VOLUME ["/config"]
USER nobody
ENTRYPOINT ["python", "-m", "mcx_web", "/config/plant.mcx", "--host", "0.0.0.0"]
