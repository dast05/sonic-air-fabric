"""Pure validation of routed TRex per-stream counters."""
def check_stats(stats,wanted):
    paths=[]
    for pgid in [100,101]:
        flow=stats.get('flow_stats',{}).get(pgid,{})
        tx_port=pgid-100; rx_port=1-tx_port
        tx=flow.get('tx_pkts',{}).get(tx_port,0)
        rx=flow.get('rx_pkts',{}).get(rx_port,0)
        paths.append({'pg_id':pgid,'tx_port':tx_port,'rx_port':rx_port,'transmitted':tx,'received':rx,'expected':wanted})
        if tx!=wanted or rx<wanted*0.95:
            raise ValueError(f'Stream {pgid}: TX={tx}, RX on opposite VNI port={rx}, expected={wanted}')
    return paths
