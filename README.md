# Raspberry Pi Rotary Phone

This is a work in progress attempt to gain full control of a rotary phone using a Raspberry Pi Zero 2 W

Here's some features I'm hoping to achieve:

### Basic

- Full handset control for mic and speaker using a USB sound card and 3.5mm solderable plugs
    - An important caveat here is I'd like to see if I can get the same sidetone effect like you'd have normally
- Hook switch to see when the phone is on/off the hook
- Dial pulse counter so I can dial numbers to do what I want like talk to Home Assistant, activate scenes, run scripts, etc.
    - I'm trying to make an easy way to define numbers and what you want them to do
- Ringer control! I love the sound of the old ringers, I'm using a 5v ring generator to run the original bell
- Full sound emulation, I want to hear the dial tone, the ringing sound (like for outgoing calls), busy signal, maybe even some fun messages
- Integration with Home Assistant

### Extras if I can figure it out

- A way to link multiple together over wifi to work like a real phone/intercom, so like I can dial 2 and call the bedroom and/or maybe send announcements to the other phones