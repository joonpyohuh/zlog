import {Config} from '@remotion/cli/config';

// Color correction (LUT, grain, bloom) happens in a later ffmpeg
// post-process step, not here — see aesthetic.* on the EDL.
Config.setVideoImageFormat('jpeg');
