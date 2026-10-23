FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39
WORKDIR /app
COPY dispatch_audit /app/dispatch_audit
# Candidate runtime: no preinstalled package manager or third-party test tooling.
RUN rm -rf /usr/local/bin/pip* /usr/local/lib/python3.13/site-packages/pip* /usr/local/lib/python3.13/site-packages/setuptools*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1
CMD ["sh", "-c", "while :; do sleep 3600; done"]
