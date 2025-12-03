FROM python:3.11-slim

# Impostiamo la cartella base generica
WORKDIR /app

# 1. Copiamo la cartella Common (Necessaria per l'ereditarietà)
# NOTA: Lo slash finale "/" è obbligatorio
COPY Common ./Common/

# 2. Copiamo il codice specifico del servizio
# Usiamo le virgolette perché la cartella sorgente ha spazi
# NOTA: Lo slash finale "/" è obbligatorio
COPY ["Timer Usage Control", "./TimerUsageControl/"]

# 3. Ci spostiamo nella cartella specifica del servizio
WORKDIR /app/TimerUsageControl

# 4. Installiamo le dipendenze
# Assumiamo che requirements.txt sia stato copiato insieme alla cartella al passo 2
RUN pip install --no-cache-dir -r requirements.txt

# Health check (Mantenuto il tuo originale)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://catalog:8001/health', timeout=5)" || exit 1

# 5. Avviamo l'applicazione
CMD ["python", "Time_Control.py"]