# Local demo media (fictional fixture)

`keplerv-demo.mp4` is a machine-generated test-pattern clip (ffmpeg `testsrc` +
440 Hz sine tone, 5 s, no speech, no people, no copyrighted material). It exists
so the media surface can demonstrate genuine local `<video>` playback without
network access or licensed content.

Regenerate with:

```sh
ffmpeg -y -f lavfi -i "testsrc=size=640x360:rate=30:duration=5" \
  -f lavfi -i "sine=frequency=440:duration=5" \
  -pix_fmt yuv420p -c:v libx264 -preset veryfast -c:a aac -shortest \
  public/media/keplerv-demo.mp4
```
