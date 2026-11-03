// AudioWorklet capture tap: forwards mono channel-0 chunks to the recorder.
class KeplerCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs?.[0]?.[0];
    if (channel && channel.length > 0) {
      this.port.postMessage(channel.slice(0));
    }
    return true;
  }
}

registerProcessor("keplerv-capture", KeplerCapture);
