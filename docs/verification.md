# Verifieringsstatus

Verifierat lokalt 2026-09-01:

- 19 automatiska tester passerar.
- Generering ger 13 noder och 18 datalänkar med unika interfaceändpunkter.
- Reciproka eBGP-peeradresser, två uplinks per leaf och fyra downlinks per spine kontrollerade.
- Båda spines har egna ASN för att även spine-loopbacks ska vara nåbara utan allowas-in.
- Routeranslutningar, TRex-accessportar och positiva inter-VNI-mål kontrollerade.
- Felaktiga loopbacks, ASN, IP, VNI, gateway och portkollisioner avvisas.
- Management, MAC, lanes och buffers bevaras i ConfigDB-merge-testet.
- Felaktigt BGP-tillstånd/ASN och utebliven TRex-mottagning på rätt port ger testfel.
- Air-instruction-fel och image utan Air-agent avvisas i tester.
- Pythonkompilering, bash-syntax och Ansible `--syntax-check` passerar.

Inte verifierat live:

- GitHub Actions-körning (verifieras efter publicering).
- Inloggning/behörigheter och imagekatalog i användarens NVIDIA Air-organisation.
- Import/start av topologin och SONiC-imageprofilens portmappning.
- NOS-preflight, ConfigDB reload, faktisk BGP/EVPN-konvergens och Linux-routing.
- TRex 3.06-installation, samtidiga strömmar och mottagningsräknare i Air.
- Hämtning av TRex-rapporter via tillfälliga Air-services.

En grön lokal testkörning är inte ett godkännande av dataplanet. Första manuella `deploy` ska köras och gå grönt innan `AIR_AUTO_DEPLOY=true` aktiveras.
