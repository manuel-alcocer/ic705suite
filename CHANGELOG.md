# CHANGELOG

<!-- version list -->

## v0.18.0 (2026-10-12)

### Bug Fixes

- **ft8**: The waterfall comes back when watched again while transmitting
  ([`5abd6ab`](https://github.com/manuel-alcocer/ic705suite/commit/5abd6ab2f87df4503099253591ee4de809811bdf))

- **gui**: A long FT8 list no longer takes the client's CPU for a minute
  ([`d189842`](https://github.com/manuel-alcocer/ic705suite/commit/d18984239e0ba7d7e167464295d44c65383727c2))

- **lotw**: Only HF goes to LoTW unless [lotw] bands says otherwise
  ([`500e74b`](https://github.com/manuel-alcocer/ic705suite/commit/500e74b7961206576d53d2a2298aa41d622e7eb3))

### Features

- **ft8**: A country column, and a super-automatic mode
  ([`69f47a8`](https://github.com/manuel-alcocer/ic705suite/commit/69f47a8aeb38d0ec957116280038abff3f9d0cef))

- **ft8**: How far the QSO has gone, and a late RR73 still logs it
  ([`87200ac`](https://github.com/manuel-alcocer/ic705suite/commit/87200ac423bddca509327d83ba798d6fe4ee311c))

- **ft8**: Where each station is, what was worked before, and a map
  ([`8a28e37`](https://github.com/manuel-alcocer/ic705suite/commit/8a28e37c27f501e31b0e435d323b8b3143d5edc2))

- **log**: A shared log refreshes by itself, and Ctrl+R refreshes it
  ([`73fd3fd`](https://github.com/manuel-alcocer/ic705suite/commit/73fd3fde44abf1bbb7b8286886164e32385eab60))

- **logbook**: One logbook for every client, on the core's machine
  ([`9c9c8b2`](https://github.com/manuel-alcocer/ic705suite/commit/9c9c8b2e9ff13e3700fb22627f05b431e24a4b33))

- **lotw**: The core signs and uploads the logbook's QSOs to LoTW, and brings back confirmations
  ([`30af1fc`](https://github.com/manuel-alcocer/ic705suite/commit/30af1fc174651a464c335cea6c756f322702c676))

- **qrz**: [qrz] api_key, and --qrz-status to check it against QRZ.com's logbook
  ([`78dbdc3`](https://github.com/manuel-alcocer/ic705suite/commit/78dbdc309e296f047b41a5cfe566b12a46bccf58))

### Testing

- LoTW's fake TQSL runs on Windows, and the late RR73 test no longer races
  ([`b56a79f`](https://github.com/manuel-alcocer/ic705suite/commit/b56a79f35091329ed921dba5515bc9080dbf2c7a))

- Two Windows races, the audio heard again and the panel editing first
  ([`4efdb92`](https://github.com/manuel-alcocer/ic705suite/commit/4efdb92202b0ea56ead9741724de6f6117149369))

- **ft8**: The late RR73 comes once the tries ran out, whatever the timers
  ([`fb9aa62`](https://github.com/manuel-alcocer/ic705suite/commit/fb9aa62b08f8bae1515471ee80d59cb4a33e054e))


## v0.17.0 (2026-10-12)

### Bug Fixes

- **station**: A half-dead radio session is connected again
  ([`457f2ea`](https://github.com/manuel-alcocer/ic705suite/commit/457f2eaa1d55a05073a81c1fddbfb27410469317))

### Features

- **ft8**: The ALC as Icom wants it, the TX level kept, a still waterfall without audio
  ([`65deca2`](https://github.com/manuel-alcocer/ic705suite/commit/65deca20b86a28c0d0b838da16fef267c4875dc7))

### Testing

- The suite's data folder kept away from the operator's (tests wrote QSOs into ft8_log.adi)
  ([`127539d`](https://github.com/manuel-alcocer/ic705suite/commit/127539d2f106473d410dc176d7cd98d1fbe7e13b))


## v0.16.2 (2026-10-12)

### Bug Fixes

- **ft8**: QSOs logged with the power measured and the setup of the radio used
  ([`3a67477`](https://github.com/manuel-alcocer/ic705suite/commit/3a67477d0f5f75daa62d26b82881a8c0bd04fce1))


## v0.16.1 (2026-10-12)

### Bug Fixes

- **swr**: The phone page at the core's address, and saved positions with their curve
  ([`3bdb3c7`](https://github.com/manuel-alcocer/ic705suite/commit/3bdb3c76f78793dc0eedb04a7734c78dbec73419))


## v0.16.0 (2026-10-12)

### Bug Fixes

- **link**: The client survives the core going away, and connects again
  ([`bdb6f99`](https://github.com/manuel-alcocer/ic705suite/commit/bdb6f9980ca85b2f6e1455a6360180517fcaec1f))

### Features

- **ft8**: Waterfall with the calls on their traces, a tune tone, quiet cycles hidden
  ([`4923e0f`](https://github.com/manuel-alcocer/ic705suite/commit/4923e0f97d0d67246afc5a7f4b834f64b6bec622))

- **ui**: CPU temperature, and the core's machine's load beside this one's
  ([`f06d211`](https://github.com/manuel-alcocer/ic705suite/commit/f06d2119abcadccb14833e1400bdd597afb463de))

### Testing

- **ft8**: The waterfall's double click answers with ft8code faked, as CI has no WSJT-X
  ([`41bdf98`](https://github.com/manuel-alcocer/ic705suite/commit/41bdf98a9f9d39cc5b67feefda1c684acb524de5))


## v0.15.0 (2026-10-12)

### Bug Fixes

- **ft8**: A message heard while ours goes out is not taken as sent
  ([`151ea52`](https://github.com/manuel-alcocer/ic705suite/commit/151ea526e452f4e7f84c4a2ef9c6738acc054ec3))

- **ft8**: Each cycle taken once when a timer wakes a little early (Windows)
  ([`731fcd6`](https://github.com/manuel-alcocer/ic705suite/commit/731fcd66aa7fe16e0dd5c016296f8979c4bf7ef2))

- **gui**: FT8 opens from the Qt dashboard and with ft8 --gui
  ([`5b5ab25`](https://github.com/manuel-alcocer/ic705suite/commit/5b5ab258ecd467d4f1b89f8078c27a35bcc5b2a7))

### Features

- **ft8**: The QSO beside the band's activity, and TX frequency, level, power and ALC at hand
  ([`5cc5b5e`](https://github.com/manuel-alcocer/ic705suite/commit/5cc5b5e3593f130ddb18b9bb8c0aa5ddd71c7421))


## v0.14.0 (2026-10-11)

### Features

- **ft8**: Transmit with WSJT-X's sequence, and each QSO logged in ADIF
  ([`b0a9383`](https://github.com/manuel-alcocer/ic705suite/commit/b0a9383239a7e8f8e6bbbd6403b4ab52f6ae3709))


## v0.13.0 (2026-10-11)

### Features

- **ft8**: The band's activity, decoded every 15 s with WSJT-X's jt9
  ([`2bc6835`](https://github.com/manuel-alcocer/ic705suite/commit/2bc6835ded0009d06ec8f61428067b613d01bd7c))

- **rig**: The radio on another machine's core, with no --connect to type
  ([`061d0b5`](https://github.com/manuel-alcocer/ic705suite/commit/061d0b57efad991a6df563736b031bbcc0009c01))

### Testing

- **ft8**: The 20 m recording jt9 is tried on (*.wav are otherwise ignored)
  ([`f265c67`](https://github.com/manuel-alcocer/ic705suite/commit/f265c67d46f7958df42a94a4ab9c11bf01cea558))


## v0.12.0 (2026-10-11)

### Features

- **packet**: A core with no radio for packet, and F7 on another machine's core
  ([`09c6b2f`](https://github.com/manuel-alcocer/ic705suite/commit/09c6b2f41dd27f35b873327aa96c0138a6b36e02))


## v0.11.0 (2026-10-11)

### Features

- **packet**: A BBS in the core, and the footer's keys clicked in the window
  ([`f2d0e75`](https://github.com/manuel-alcocer/ic705suite/commit/f2d0e75b7ac7a279cecb12b1a9c4e1126428f98e))


## v0.10.0 (2026-10-11)

### Features

- **packet**: The IC-705 over WiFi as the packet radio, with Direwolf here
  ([`5ab9d1f`](https://github.com/manuel-alcocer/ic705suite/commit/5ab9d1fcfcb4b754ca900beb1b5c52b926fba4d1))


## v0.9.0 (2026-10-11)

### Features

- **packet**: The core can start its own Direwolf, with the Digirig found here
  ([`8e7188c`](https://github.com/manuel-alcocer/ic705suite/commit/8e7188cf8a2731461c2feb5990c6a9a8092f9bba))


## v0.8.0 (2026-10-11)

### Features

- **packet**: BayCom-style terminal on a Direwolf TNC kept by the core
  ([`d9972b2`](https://github.com/manuel-alcocer/ic705suite/commit/d9972b2d26cb94d49f406f2d0821237682f8bb18))


## v0.7.0 (2026-10-11)

### Features

- **gui**: Theme and help as buttons: sun, moon or auto, and ? for About
  ([`dac65f1`](https://github.com/manuel-alcocer/ic705suite/commit/dac65f1cda1b9a2ef170c55b128ac2095b6fb7bd))


## v0.6.0 (2026-10-11)

### Features

- **swr**: The chosen band's history and its latest curve beside the bands
  ([`048903f`](https://github.com/manuel-alcocer/ic705suite/commit/048903f5d8047260540d6de4403473c54035f56f))


## v0.5.0 (2026-10-11)

### Features

- **cw**: The macro keys alone, and ? for what each sends
  ([`88050fa`](https://github.com/manuel-alcocer/ic705suite/commit/88050fad57fef9b4d6f9b56f3ec61d7b187cf48e))

- **gui**: View > Theme switches dark, light or the system's at once
  ([`ee1792b`](https://github.com/manuel-alcocer/ic705suite/commit/ee1792b7a9a9e4a213109f73c30fc6682a3bc667))

- **swr**: The phone switches the remote control on with a password
  ([`74b6514`](https://github.com/manuel-alcocer/ic705suite/commit/74b6514ed153d66673863b1857e9a48750ba0784))

- **view**: The front panel with knobs, switches and drop-downs
  ([`257c414`](https://github.com/manuel-alcocer/ic705suite/commit/257c4148c93e62dbe8b34c966d01e07fa7def71d))


## v0.4.0 (2026-10-11)

### Features

- **log**: Duplicates are by day, with a rule per profile
  ([`6019fc3`](https://github.com/manuel-alcocer/ic705suite/commit/6019fc35cee1e2ceb5518a608c43fc742e2b3b0c))

- **ui**: The version always shown, and a Help menu with About
  ([`43e7493`](https://github.com/manuel-alcocer/ic705suite/commit/43e7493dd64060e0625756eebb3b3248fd02b7d0))


## v0.3.1 (2026-10-11)

### Bug Fixes

- **rig**: The transmit meters follow the radio, however it is keyed
  ([`ce82b73`](https://github.com/manuel-alcocer/ic705suite/commit/ce82b73ec46671baa87f325fb81001711d795909))

### Testing

- **cw**: The skimmer's scope is checked once it is laid out
  ([`ff06fd4`](https://github.com/manuel-alcocer/ic705suite/commit/ff06fd4b521d67e7c821b76a67f62fd48fb74f6c))


## v0.3.0 (2026-10-11)

### Features

- **gui**: A light theme, or the desktop's choice
  ([`8cdc660`](https://github.com/manuel-alcocer/ic705suite/commit/8cdc6606488d255bcb3c9bbee9ed01758b483856))

- **swr**: A history to browse, with photos
  ([`eada85e`](https://github.com/manuel-alcocer/ic705suite/commit/eada85e77df4209bdc6e5e8ac6e7be38ef37d666))

- **swr**: Presets, your own targets besides the bands' own
  ([`d0942b7`](https://github.com/manuel-alcocer/ic705suite/commit/d0942b7c0809ec2854425337fddb37f8b2cbe645))


## v0.2.1 (2026-10-11)

### Bug Fixes

- **windows**: The release's smoke test waits for the suite to say it is ready
  ([`fdece83`](https://github.com/manuel-alcocer/ic705suite/commit/fdece834e614f8a39826bd442c96e71cebc74443))


## v0.2.0 (2026-10-11)

### Continuous Integration

- **release**: Sign each released file with its build provenance
  ([`f15264f`](https://github.com/manuel-alcocer/ic705suite/commit/f15264f95e9c84da33d34fb94409aa3fc56c3601))

### Features

- **gui**: A Qt graphical client, the terminal suite in a window with the mouse
  ([`1d94538`](https://github.com/manuel-alcocer/ic705suite/commit/1d94538bc3528685c65158bd91683631ac12d7f0))


## v0.1.0 (2026-10-11)

- Initial Release
