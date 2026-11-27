# Photobooth for the virtual test setup: built from the source under test
# (context = fork working tree, or a clone of another ref), with photobooth-setup's glue (fake gphoto2, CUPS client
# config, apply-config) taken from the `setup` context, as in the deployed
# photobooth-setup/photobooth/Dockerfile. Keep the package list and the
# RemoteStorageService patch in step with that file.
FROM webdevops/php-apache:8.4

RUN echo "LimitRequestLine 12000" > /opt/docker/etc/httpd/conf.d/limits.conf \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cups-client \
        curl \
        git \
        gnupg \
        libimage-exiftool-perl \
        nodejs \
        python3 \
        rsync \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=setup gphoto2 /usr/local/bin/gphoto2
COPY --from=setup client.conf /etc/cups/client.conf
COPY --from=setup apply-config.php /opt/photobooth/apply-config.php
COPY --from=setup apply-config.sh /opt/docker/provision/entrypoint.d/50-photobooth-config.sh
# The printer status query runs on the booth host in production
# (BACKEND=canonselphyneo gutenprint53+usb -s); here the same command line
# reaches the simulated SELPHY in the cups container.
COPY --from=sim selphy_sim.py /usr/lib/cups/backend/gutenprint53+usb
RUN chmod 755 /usr/local/bin/gphoto2 /opt/docker/provision/entrypoint.d/50-photobooth-config.sh \
        /usr/lib/cups/backend/gutenprint53+usb \
    && echo 'SIM_QUERY_URL=http://cups:8631' >> /etc/environment

WORKDIR /app
COPY --chown=application:application . .
USER application
# Same patch as photobooth-setup/photobooth/Dockerfile: RemoteStorageService
# reads the removed colors.button_font key and the PHP warning corrupts JSON.
RUN git config --global --add safe.directory /app \
    && sed -i "s/\$config\['colors'\]\['button_font'\]/\$config['fonts']['button_font_color'] ?? '#ffffff'/" src/Service/RemoteStorageService.php \
    && git submodule update --init --depth 1 \
    && npm install \
    && npm run build \
    && mkdir -p data config \
    && touch welcome/.skip_welcome

USER root
