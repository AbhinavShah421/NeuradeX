#!/usr/bin/env bash
# Elasticsearch index template for the container-log index Filebeat writes.
#
# Kept OUT of filebeat.yml deliberately. Filebeat's bundled ECS template maps
# `service` as an object (service.name, service.version, ...), but the app's
# elk_logger writes `service` as a plain string. With the ECS template in place
# Elasticsearch rejected every event —
#   "object mapping for [service] tried to parse field [service] as object,
#    but found a concrete value"
# — and dropped them, leaving an index with zero documents and no obvious cause.
#
# Mapping `service` as a keyword here means ONE query shape works across both
# neuradex-docker-* and neuradex-logs-*, which is what lets the System Map use a
# single Kibana link per component.
#
# Also note: no `data_stream` section. Filebeat defaults to data streams, whose
# backing indices are named `.ds-*` and which cannot be reshaped afterwards
# without deleting them first.
#
# Idempotent — safe to re-run. Requires the stack to be up.
set -euo pipefail

ES="${ELASTICSEARCH_URL:-http://localhost:9200}"

echo "Installing neuradex-docker index template on ${ES} ..."
code=$(curl -s -o /tmp/es_tmpl_out -w '%{http_code}' -X PUT "${ES}/_index_template/neuradex-docker" \
  -H 'Content-Type: application/json' -d '{
  "index_patterns": ["neuradex-docker-*"],
  "priority": 500,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "service":    { "type": "keyword" },
        "level":      { "type": "keyword" },
        "stream":     { "type": "keyword" },
        "message":    { "type": "text" }
      }
    }
  }
}')

if [ "$code" = "200" ]; then
  echo "  ok"
else
  echo "  FAILED (HTTP $code)"
  cat /tmp/es_tmpl_out
  echo
  echo "  If this complains about existing data streams, stop filebeat, run:"
  echo "    curl -XDELETE '${ES}/_data_stream/neuradex-docker-*'"
  echo "    curl -XDELETE '${ES}/_index_template/neuradex-docker'"
  echo "  then re-run this script and start filebeat again."
  exit 1
fi
