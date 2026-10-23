\pset pager off
DROP TABLE IF EXISTS cab;
CREATE TEMP TABLE cab AS
SELECT d.id,
       d.created_at::date AS d_date,
       (d.cf_pnl_pct > 0)::int AS y,
       d.cf_pnl_pct AS ret,
       COALESCE(SUM((sig->>'confidence')::float*(sig->>'weight')::float)
                FILTER (WHERE sig->>'action'='BUY'),0)  AS bm,
       COALESCE(SUM((sig->>'confidence')::float*(sig->>'weight')::float)
                FILTER (WHERE sig->>'action'='SELL'),0) AS sm,
       COALESCE(SUM((sig->>'confidence')::float*(sig->>'weight')::float)
                FILTER (WHERE sig->>'action'='HOLD'),0) AS hm,
       COUNT(*) FILTER (WHERE sig->>'action'='BUY')  AS bn,
       COUNT(*) FILTER (WHERE sig->>'action'='SELL') AS sn,
       COUNT(*) AS tn
FROM session_decisions d
CROSS JOIN LATERAL jsonb_array_elements(d.agents) sig
WHERE d.cf_pnl_pct IS NOT NULL
  AND d.candle_time < '13:00'
  AND d.agents IS NOT NULL
GROUP BY d.id, d.created_at, d.cf_pnl_pct;

\copy (SELECT d_date,y,ret,bm,sm,hm,bn,sn,tn FROM cab) TO STDOUT WITH CSV HEADER
