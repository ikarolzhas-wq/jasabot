FROM python:3.11-slim

WORKDIR /bot
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# torch-тың CPU нұсқасы: сервер үшін жеткілікті әрі әлдеқайда жеңіл
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install -r requirements.txt

# Embedding моделін сурет құрастыру кезінде жүктеп қоямыз (іске қосу тез болады)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY . .

EXPOSE 8000
# Іске қосылғанда алдымен білім базасын жаңартады, содан кейін серверді қосады
CMD python -m scripts.build_index && uvicorn app.main:app --host 0.0.0.0 --port 8000
