FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY faultline ./faultline
COPY web ./web
COPY tests ./tests
# the landing page's numbers are tested against this file
COPY docs/rcaeval-results.md ./docs/
EXPOSE 8090
CMD ["uvicorn", "faultline.api:app", "--host", "0.0.0.0", "--port", "8090"]
