FROM --platform=linux/x86-64 python:3.10.0
WORKDIR /usr/src/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn", "email_marketing_ms_django_backend.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
