# Translations

This directory holds `.po` / `.mo` message catalogues for the plugin.

To extract the current strings from templates and Python source:

```bash
cd pretix_event_analytics
django-admin makemessages -l en -l de
```

…and compile after editing:

```bash
django-admin compilemessages
```

Target locales are set in the `locale/<lang>/LC_MESSAGES/` layout. Add more
by creating sibling directories and re-running `makemessages`.
