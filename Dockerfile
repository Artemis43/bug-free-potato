FROM python:3.11-slim
WORKDIR /medbot
COPY . /medbot/
RUN pip install -r requirements.txt
EXPOSE 4343
CMD python main.py