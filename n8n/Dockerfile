FROM n8nio/n8n:2.16.0

USER root
COPY patch-instance-settings.cjs /opt/kai/patch-instance-settings.cjs
RUN node /opt/kai/patch-instance-settings.cjs \
    && rm /opt/kai/patch-instance-settings.cjs
COPY secret-entrypoint.sh /opt/kai/secret-entrypoint.sh
COPY scrub-settings.cjs /opt/kai/scrub-settings.cjs
RUN chmod 0755 /opt/kai/secret-entrypoint.sh
USER node

ENTRYPOINT ["tini", "--", "/opt/kai/secret-entrypoint.sh"]
