# SONiC fabric på NVIDIA DSX Air

Git är källan för designen. YAML genererar Air-topologi, SONiC ConfigDB, FRR-konfiguration och tester. GitHub Actions validerar varje PR och kan konfigurera labbet efter merge till `main`.

**Status:** YAML-modeller, konfigurationsgenerering och CI/CD finns i detta repo. Ingen Air-simulering har verifierats live ännu. Börja med att konfigurera `air-lab` enligt avsnittet **Anslut NVIDIA Air**. Portmappning, imagekompatibilitet och TRex verifieras av första livekörningen; lokala tester bevisar inte dataplanet.

## Topologi

| Nod | Roll | Anslutning |
|---|---|---|
| spine01–02 | eBGP underlay och EVPN | Alla fyra leafs |
| leaf01–04 | SONiC VXLAN VTEP | Båda spines; egen loopback som VTEP-adress |
| server01–02 | Linux-testservrar | VNI 10100, VLAN 100, 192.168.100.0/24 |
| server03–04 | Linux-testservrar | VNI 10200, VLAN 200, 192.168.200.0/24 |
| trex01 | TRex STL | Red på leaf01; blue på leaf03 |
| trex02 | TRex STL | Red på leaf02; blue på leaf04 |
| router01 | Linux-router, IPv4 forwarding | Red på leaf01; blue på leaf03 |

Totalt **13 noder och 18 datalänkar**, plus automatiskt OOB-nät. Varje leaf har en länk till varje spine. Ingen spine-spine- eller leaf-leaf-länk behövs.

| Nät | VLAN | VNI | Gateway | Router-MAC |
|---|---:|---:|---|---|
| red | 100 | 10100 | 192.168.100.1 | 02:00:00:aa:00:01 |
| blue | 200 | 10200 | 192.168.200.1 | 02:00:00:bb:00:01 |

TRex genererar samtidigt `trex01/red → router01 → trex02/blue` och `trex01/blue → router01 → trex02/red`, samt motsatta riktningarna. Mottagning mäts på motsatt nätverksport med TRex stream-räknare, inte enbart med generella interface counters. Routern har statiska ARP-poster för TRex-portarna, eftersom STL inte behöver svara på ARP.

Det är **extern inter-VNI-routing**, inte distribuerad anycast gateway eller EVPN L3VNI/VRF route leaking. Routern tillåter trafik mellan näten och är en avsiktlig single point of failure i första rundan. Inga isolationstester för blockerad L3-trafik utlovas i den här designen.

## Kom igång med repot

```bash
git clone https://github.com/dast05/sonic-air-fabric.git
cd sonic-air-fabric
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make test
make render
```

Det här repot är publikt och innehåller en syntetisk labbdesign. NGC-nycklar och SSH-nycklar konfigureras som GitHub Secrets enligt nedan.

## Anslut NVIDIA Air

1. Logga in på [DSX Air](https://dsx-air.nvidia.com/) och säkerställ att din NGC-organisation har Air-tillgång och tillräcklig kvot.
2. Skapa en NGC API-nyckel med NVIDIA Air tilldelat. För långlivad CI/CD är en organisationsägd Service Key lämplig. Nyckeln behöver access, image read samt simulation read/create/edit/start-stop. Simulation delete behövs inte för normal drift; tillfälliga report services tas bort efter läsning.
3. Kör `bash scripts/configure-github.sh`. `gh secret set` tar emot nyckeln säkert och lägger den som `NGC_API_KEY` i GitHub Environment **air-lab**. Lägg aldrig nyckeln i YAML eller chatten.
4. Skriptet startar workflow **NVIDIA Air lab → discover**. Hämta `images.json` från dess artifact. Den innehåller namn, UUID, agentstöd, standardanvändare och minnes/CPU-krav; inga lösenord.
5. Skapa följande **Environment variables** i `air-lab`:

| Variabel | Värde |
|---|---|
| `AIR_SONIC_IMAGE` | Exakt UUID för kompatibel community SONiC virtual switch |
| `AIR_HOST_IMAGE` | Exakt UUID för Ubuntu för server01–04 och router01 |
| `AIR_TREX_IMAGE` | Exakt UUID för Ubuntu **22.04**, Python 3.10 |
| `AIR_TRANSPORT` | `agent` eller `ssh` |
| `TREX_ARCHIVE_SHA256` | Valfri SHA256 för godkänd TRex 3.06-archive |

6. För `agent` måste **alla tre images** ha `includes_air_agent: true`. Annars använd SSH-alternativet nedan. Välj inte en annan NOS bara för att den har agentstöd.
7. Kör manuellt **NVIDIA Air lab → deploy** på `main`.
8. När första körningen går grönt: sätt **repository variable** `AIR_AUTO_DEPLOY=true`. Den måste ligga på repo-nivå eftersom den läses innan jobbet startar. Därefter kör varje push/merge till `main` full deployment och test.

PR-jobbet har inga Air-hemligheter. Det kör validering, generering och unit tests. `main`-jobbet upprepar dessa kontroller innan deployment, så CD kan inte springa förbi en misslyckad lokal validering. Endast en labbkörning åt gången tillåts.

## Image och resurser

Referensprofilen följer NVIDIA:s offentliga SONiC EVPN-demo: **community SONiC, split-unified**, med FRR i `/etc/sonic/frr/frr.conf`. `sonic-vs-202305` är en referenshint, inte ett löfte om att imagen finns i nuvarande Air-katalog. Exakta UUID:er kommer från discover. Profilen passar inte automatiskt Dell Enterprise SONiC eller andra leverantörsvarianter.

Planerad baskvot: **26 vCPU, 38 GiB RAM, 210 GB disk**, plus Airs OOB-noder. Air-imagekrav kan kräva större tilldelning. Justera YAML till imagen och er kvot före import.

| Leaf-funktion | Air VM-interface | SONiC-port |
|---|---|---|
| Testserver | eth1 | Ethernet0 |
| TRex | eth2 | Ethernet4 |
| spine01 | eth3 | Ethernet8 |
| spine02 | eth4 | Ethernet12 |
| Router, leaf01/03 | eth5 | Ethernet16 |

Spines använder eth1–4 ↔ Ethernet0/4/8/12. Verifiera denna mappning på vald image. Koden bevarar befintliga port-lanes, speed, plattforms-MAC, buffers och management; fabricägda tabeller ersätts helt så att borttagna nät inte lever kvar. Befintliga PortChannels och frr-mgmt-framework ger ett preflight-fel.

## SSH/Ansible om imagen saknar Air-agent

Air-API skapar och startar labbet; Ansible sköter konfiguration och tester. Det kräver en initial SSH-bootstrap:

1. Kör workflow `provision` för att skapa/starta topologin utan agentkrav.
2. Aktivera SSH-service till `oob-mgmt-server` i Air. Registrera din SSH-nyckel där och på respektive switch/Linux-host med Air-konsolen eller en godkänd image/bootstrap. Använd bildens riktiga användare, inte antagna standardlösenord.
3. Verifiera host keys via Air-konsolen och lägg dem i `AIR_SSH_KNOWN_HOSTS`. Använd inte `StrictHostKeyChecking=no`.
4. Skapa en inventory utifrån `ansible/inventory.example.yml` med managementadresser och korrekt användare. Om GitHub-hostad runner används, lägg till `ansible_ssh_common_args` med `ProxyJump` till det observerade Air-SSH-endpointet. Jump- och nodnycklar måste kunna användas av runnern. Bli root genom lösenordsfri sudo eller en organisationsgodkänd Ansible-konfiguration.
5. Spara inventory som `AIR_SSH_INVENTORY` och privatnyckeln som `AIR_SSH_PRIVATE_KEY` i Environment secrets. Privata nyckeln ska inte lämnas i chatten eller committas.
6. Sätt `AIR_TRANSPORT=ssh` och kör `deploy`. SSH-host keys kontrolleras. Inventory/nyckeln tas bort från runnern efter jobbet och laddas inte upp som artifact.

Du kan också köra samma playbook direkt från en kontrollhost med managementåtkomst:

```bash
pip install -r ansible/requirements.txt
python -m fabriclab render
ansible-playbook -i ansible/inventory.local.yml ansible/lab.yml
```

`provision` kan inte automatiskt förbereda SSH på en image utan agent eller fungerande ZTP. Detta är en explicit initial installationsgräns; pipelinen försöker inte gissa lösenord.

## Dagligt arbetsflöde

| Fil | Vad ändras här? |
|---|---|
| `models/fabric.yml` | ASN, loopbacks, /31-pool, VLAN/VNI och vanliga hosts |
| `models/platform.yml` | Air/SONiC-portmappning, imagehints och resurser |
| `models/routing.yml` | Routerns anslutningar, gateways och MAC-adresser |
| `models/traffic.yml` | TRex-hostar, adresser, PPS, packet size och duration |
| `templates/frr.conf.j2` | Routingmall för denna SONiC-profil |

```bash
make test
make render
# Review build/topology.json, build/<node>/frr.conf and ConfigDB overlays.
```

Byt inte ut image-UUID eller topologi i en existerande simulering utan att planera det. Provisioneringen jämför ett topologihash och stoppar vid skillnad; ändra `fabric.name` om du vill skapa en ny parallell labb. Ett befintligt labb med samma namn men annan ägare/repo tas aldrig över. Genererade filer är build-artifacts och ska inte redigeras som källa.

`deploy` använder full ConfigDB reload: avbrott under konfigurationsfasen är väntade i labbet. Vid fel i själva reload återställs den berörda nodens backup automatiskt. Ett senare testfel återställer **inte hela fabricen** automatiskt; kör om senaste fungerande Git-revision eller återställ en kontrollerad checkpoint.

## TRex och testresultat

TRex **3.06**, Ubuntu 22.04/Python 3.10, STL och Linux AF_PACKET. Inga PCI-passthrough-enheter, hugepages eller NIC-ombindningar krävs i detta läge. Källan är Ciscos versionsbestämda HTTPS-archive. SHA256 registreras på hosten och kan låsas med variabeln ovan. Nodes behöver utgående internet för första installationen och tidsynkroniserade klockor för gemensam teststart.

Standard: fyra samtidiga riktade strömmar, **1 000 pps per ström**, **10 sekunder**, **256 byte inklusive FCS i storleksmodellen**. Gränsen för godkänt är alla avsedda TX-paket och minst 95% RX på rätt mottagarport. Första körningen kan behöva justera TRex-profilen eller imagen; resultat blir inte godkänt genom att bara ignorera en misslyckad ström.

| Kontroll | Vad måste fungera? |
|---|---|
| Intent | Unika loopbacks/leaf-ASN, giltiga IP, VLAN/VNI och portar |
| BGP | Exakt förväntad peerlista, rätt ASN, Established |
| EVPN | Samma grannar Established i L2VPN EVPN AF |
| Underlay | Alla switch-loopbacks nåbara |
| Hosts | Ping inom VNI och routad ping till andra VNI |
| TRex | Stream 100/101 når motsatt VNI-port via router01 |

GitHub-artifacten innehåller topologi, genererad konfiguration, JUnit-resultat, simulation-ID och, efter lyckad trafikkörning, mätningar från båda TRex-hostarna. API-transporten öppnar tillfälligt en HTTP-service för **endast syntetiska trafikrapporter**, hämtar dem och tar sedan bort servicen. TRex RPC exponeras inte externt. På Air-VM:arna finns rapporter i `/var/lib/fabriclab/reports/trex.json` och SONiC-backuper i `/var/lib/fabriclab/backups/`.

Labbets auto-sleep sätts till fyra timmar efter start av deploy/provision. Ingen automatisk radering. `stop` sparar en checkpoint. Linux-interfacekonfiguration och TRex service sparas för nästa boot. Ändra vilotiden i koden om labbet ska vara igång längre.

## Felsökning

- **Image missing/agent false:** kör discover; använd riktig image UUID och rätt transport.
- **Port saknas:** korrigera `platform.yml` efter att ha verifierat Air-NIC/SONiC-portmappningen.
- **BGP fungerar men dataplanet gör det inte:** kontrollera VM-portarnas MTU, VXLAN/FDB och den valda virtual image-versionen. NVIDIA:s äldre demo innehåller en ebtables-workaround. Den här implementationen raderar inga firewallregler automatiskt; granska den specifika drop-regeln innan en ändring införs.
- **TRex startar inte:** läs `journalctl -u trex`; kontrollera Ubuntu-version, archive-tillgång, AF_PACKET och CPU-resurser.
- **TRex RX saknas:** kontrollera routerns `ip route`/`ip neigh`, VLAN-portarna och `show vxlan`/EVPN. Statisk ARP för TRex återinstalleras vid deploy.
- **Clock/start time:** båda TRex-hostarna måste ha synkroniserad tid. Missad start ger fel och kräver en ny körning.
- **Timeout:** Air kan ange ACTIVE innan NOS och agent är redo. Inspektera den specifika node instruction som rapporterats i workflowloggen.

Detta är ett funktionellt, virtuellt labb. Ingen fysisk ASIC-, buffert-, RoCE-, line-rate- eller HA-verifiering är gjord.

## Referenser

- [NVIDIA Air SDK](https://docs.nvidia.com/air/sdk/latest/)
- [NGC/Air authentication](https://docs.nvidia.com/networking-ethernet-software/nvidia-air-v2/Authentication/)
- [Air topology import](https://docs.nvidia.com/air/sdk/latest/examples/import_export.html)
- [Air node instructions](https://docs.nvidia.com/air/sdk/latest/api/air_sdk/endpoints/node_instructions/index.html)
- [NVIDIA SONiC EVPN reference lab](https://gitlab.com/cumulus-consulting/goldenturtle/sonic-demo-evpn-vxlan)
- [TRex native Linux interface documentation](https://github.com/cisco-system-traffic-generator/trex-core/blob/master/doc/trex_book.asciidoc)
- [TRex 3.06 release notes](https://github.com/cisco-system-traffic-generator/trex-core/blob/master/doc/release_notes.asciidoc)
