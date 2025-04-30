# Hassarr Integration

Hassarr is a custom Home Assistant integration to add movies and TV shows to Radarr, Sonarr and Overseerr.

## Requirements

* You must have Home Assistant OS or Home Assistant Core installed (I only tested it on Home Assistant Core)
* HACS installed on Home Assistant (instructions below for Home Assistant Core in Docker)

## Installation


### Installing HACS on Home Assistant Core in Docker
1) SSH into your server (if you have one)
2) SSH into your homeassistant docker container
`docker exec -it <your_container_name> bash`
3) Run the following command to install HACS
`wget -O - https://get.hacs.xyz | bash -`
4) Exit from the docker container
`exit`
4) Restart the docker container
`docker-compose restart <your_container_name>`
5) Add the HACS integration by going to <your_hass_ip>:<your_hass_port>/config/integrations/dashboard on the browser
6) Press "+ Add Integration"
7) Look up "HACS"
8) Read and check all the boxes and Add

Now you should have the HACS button showing on the left menu, which should bring you to the HACS dashboard

### Install Hassarr on HACS
1) Press the button to add this custom repo to HACS
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TegridyTate&repository=Hassarr&category=Integration)
2) Now look up "Hassarr" in the HACS Store search bar and download it
3) Restart your home assistant again for Hassarr to be properly added: `docker-compose restart <your_container_name>`
4) Add Hassarr to your Home Assistant integrations: <your_hass_ip>:<your_hass_port>/config/integrations/dashboard
5) Press "+ Add Integration"
6) Look up "Hassarr" and select it
7) It should prompt you to pick either Radarr & Sonarr, or Overseerr. Pick whichever service(s) you want to use and follow the instructions.
8a) For Radarr & Sonarr, after filling in the urls and api keys, it will prompt you with the quality profiles you want to use for each service.
8b) For Overseerr, after filling in the urls and api keys, it will prompt you with the Overseerr user you want to use for making requests. 

Now Hassarr should be installed, and you can create an Automation or Intent to have sentences trigger downloads on Sonarr and Radarr.

### How do I add an Automation, and what is an Automation (for noobies)?
Good question! I'm not even sure entirely what Automations are capable of precisely, but with Hassarr you're able to map a sentence to a Hassarr action, like Add Movie or Add TV Show.

You can set something up like "Add {some_title} to Radarr for me please" and this will trigger it to download your title on Radarr.

There's two ways of adding this, through the UI or directly into your automations.yaml file.

#### Adding Automations in the UI
1) In Home Assistant, go to Settings > Automations & Scenes > + Create Automation > Create New Automation
2) + Add Trigger > Sentence, and fill in something like this `Download {title} for me on radar`. It's important to write `radar` instead of `radarr` as your speech-to-text will always transcribe the spoken word `radar` to `radar`, and not with `rr`. Add multiple sentences if you want multiple phrases to trigger it to add a movie to Radarr. Same applies to Sonarr or Overseerr.
3) + Add Action > Type in `Hassarr` > Select `Hassarr: add_movie` > Press the three vertical dots > `Edit in YAML` > and fill in the following into the YAML editor
    ```
    action: hassarr.add_radarr_movie
    metadata: {}
    data:
      title: "{{ trigger.slots.title }}"
    ```
4) Hit Save, give it a name like `Add Movie to Radarr`
5) Repeat steps 1-4 for Sonarr (or do it for Overseerr for movies and tv shows, if you prefer)

Now you should be able to add a movie or TV show to Radarr and Sonarr using the sentences you setup!

#### Adding Automations in YAML
1) Open the `automations.yaml` in your home assistant's `config` directory, or wherever you mount your home assistant's docker container
2) Paste in the following
```
- id: '1734867354703'
  alias: Add movie using Assist
  description: ''
  triggers:
  - trigger: conversation
    command:
    - (Download|Add|Send) movie {title} [on|to]  [radarr|radar]
    - (Baixa|Adiciona|Envia)[r] [o] filme {title} [no|ao|para o|para] [radarr|radar]
  conditions: []
  actions:
  - action: hassarr.add_radarr_movie
    metadata: {}
      data:
        title: ""{{ trigger.slots.title }}""
  mode: single
```
You can change the sentences in `command: ` to whatever sentences you like, add more etc.
3) Save the file, and you're good to go.
4) Make a copy of this for Sonarr (or for Overseerr, one for movies and one for tv shows, if you so prefer)

Shoutout to the [repo by Github user Avraham](https://github.com/avraham/hass_radarr_sonarr_search_by_voice) for trying this some time ago, but unfortunately I had difficulties trying to get this to work.
Make sure to check the [Template sentence syntax](https://developers.home-assistant.io/docs/voice/intent-recognition/template-sentence-syntax/) to understant how to change the activation commands.
