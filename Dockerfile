FROM python:3.12-slim
WORKDIR /app
COPY server.py controller.py tonly.py analytics.py index.html strips.json manifest.webmanifest icon.svg sw.js ./
# strips.json is bind-mounted at runtime for persistence; the copy is a seed.
EXPOSE 8080 10086
CMD ["python3", "server.py"]
