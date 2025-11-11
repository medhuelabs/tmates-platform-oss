#!/bin/bash
set -e

echo "🔄 Renewing SSL certificates..."

# Renew certificates
docker run --rm \
  -v $(pwd)/certbot_www:/var/www/certbot \
  -v $(pwd)/certbot_certs:/etc/letsencrypt \
  certbot/certbot renew

# Restart nginx to reload certificates
docker compose -f docker-compose.prod.yml restart nginx

echo "✅ SSL certificate renewal complete!"