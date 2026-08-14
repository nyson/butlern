## Rework RSVP module into a new module

* Keep stores
* Rewrite views and logic from scratch
* Use a functional core, imperative shell approach where the shell is called from the views
* Views should be as thin as possible - only routing

## Rework plugin registrations
* Import a module/register.py for bot_events instead of passing through all variables manually
* Make shared stores

## Standardise logging

* Use python logging for both discord.py and our logging