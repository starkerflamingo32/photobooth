# The deployed CUPS image (photobooth-setup cups/, built as the `cups-base`
# context) with the printer replaced by the simulated SELPHY. Queue options,
# PPD, cupsd.conf and the setup's entrypoint stay exactly as deployed
# (NFR-13.1); only the device and the Gutenprint USB backend are swapped.
FROM cups-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-yaml \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY selphy_sim.py /opt/selphy-sim/selphy_sim.py
COPY sim-entrypoint.sh /usr/local/bin/sim-entrypoint.sh
# The real backend stays next to it for reference; the shim answers device
# discovery (for the setup's entrypoint) and the standalone status query (-s).
RUN mv /usr/lib/cups/backend/gutenprint53+usb /usr/lib/cups/backend/gutenprint53+usb.real \
    && ln -s /opt/selphy-sim/selphy_sim.py /usr/lib/cups/backend/gutenprint53+usb \
    && ln -s /opt/selphy-sim/selphy_sim.py /usr/lib/cups/backend/sim-selphy \
    && ln -s /opt/selphy-sim/selphy_sim.py /usr/local/bin/selphy-sim \
    && chmod 755 /opt/selphy-sim/selphy_sim.py /usr/local/bin/sim-entrypoint.sh \
    && sed -i 's/^LogLevel .*/LogLevel debug/' /etc/cups/cupsd.conf \
    && gs -q -sDEVICE=jpeg -r100 -g1500x1000 -o /opt/selphy-sim/test-photo.jpg \
        -c "0.2 0.5 0.8 setrgbcolor clippath fill 1 setgray /Helvetica findfont 90 scalefont setfont 200 450 moveto (selphy-sim test photo) show showpage"

ENTRYPOINT ["/usr/local/bin/sim-entrypoint.sh"]
