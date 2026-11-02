# Visual inspiration

The primary reference is Bradley Tangonan's [video as a 3D object with two
spatial dimensions and one time dimension](https://www.linkedin.com/feed/update/urn:li:activity:7505683227077177345/).
His [slit-scan follow-up](https://www.linkedin.com/posts/bradleytangonan_xy-t-y-3d-visualization-of-the-slit-activity-7508729350012170241-77zv)
shows how angled slices can combine different parts of different moments.

Recall uses a simpler, evidence-first version: still frames are sampled
from the recording, stacked along time, and navigated with a current-time
slice. Side surfaces show narrow strips taken from those sampled frames. The
bands mark sampling moments; they do not create additional observed frames.
The app does not reconstruct the physical 3D scene or implement slit-scan
interpolation or a predictive physics model.

Other references considered:

- [LaGSplat](https://louenpottier.github.io/lagsplat.html) uses learned latent
  dynamics and Gaussian splatting for interactive physics. Its clear connection
  between a volume, a selected state and a rendered frame influenced the way
  Recall connects a cube selection to the evidence player. The underlying
  technique is a different research system and is not included here.
- [TIMECUBE](https://github.com/yitzilitt/TIMECUBE) explores slicing a video
  volume as a creative tool.
- [Processing's slit-scan example by Golan Levin](https://github.com/processing/processing-video/blob/main/examples/Capture/SlitScan/SlitScan.pde)
  demonstrates building a time image from successive video slices.
- [Tube](https://github.com/amanibobo/tube), the original project provided for
  inspiration, connects questions to visual evidence through video time.

No assets or source code from these references were copied into the app.
