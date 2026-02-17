FROM --platform=linux/amd64 debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://enterprise.proxmox.com/debian/proxmox-release-bookworm.gpg \
       -o /etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg \
    && echo "deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription" \
       > /etc/apt/sources.list.d/pve.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    pve-qemu-kvm \
    libguestfs-tools \
    linux-image-amd64 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /backups
ENTRYPOINT ["/bin/bash"]
