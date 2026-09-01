# Fabric och trafikvägar

```mermaid
flowchart TB
  subgraph Underlay
    S1[spine01]
    S2[spine02]
    L1[leaf01]
    L2[leaf02]
    L3[leaf03]
    L4[leaf04]
    S1 --- L1 & L2 & L3 & L4
    S2 --- L1 & L2 & L3 & L4
  end
```

```mermaid
flowchart TB
  T1[trex01]
  R["VNI 10100 / red"]
  B["VNI 10200 / blue"]
  GW[router01]
  T2[trex02]
  T1 --- R
  T1 --- B
  R --- GW
  GW --- B
  R --- T2
  B --- T2
```

Varje TRex har två dataportar. Trafikprofilen använder gateway-MAC och mål-IP i **det andra** subnätet. Routern har ett dedikerat interface per VNI och routar IPv4 mellan dem. EVPN/VXLAN distribuerar L2-segmenten över fabricen. Managementtrafik går separat i Airs OOB-nät.
