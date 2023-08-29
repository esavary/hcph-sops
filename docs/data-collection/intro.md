The goal of the experimental setting is to obtain several functional MRI tasks and a long diffusion MRI scan with synchronized physiological recordings, including gas contents with a gas analyzer (GA), eye tracking (ET; including the right eye position, pupil size, blinks, etc.), respiration tracking through a pneumatic respiration belt (RB), and finally MRI-compatible electrocardiogram (ECG).

The overall experimental setting can be sumarized as follows:

``` mermaid
flowchart TB

    subgraph "Scanning Room"
        direction TB
        sr1[Scanner]
        sr2[Cannula]
        sr3[RB]
        sr4[ECG]
        sr5[ET]
    end

    subgraph "BIOPAC"
        direction TB
        biopac1[DA100C]
        biopac2[ECG100C MRI]
        biopac3[AMI100D]
        biopac4[STP100D]
    end

    sr1 --->|Trigger| sb[Syncbox]
    sr2 ---> ga[Gas Analyzer]
    sr3 --->|"Negative (-)"| biopac1[DA100C]
    sr4 ---> biopac2[ECG100C MRI]
    sr5 ---> et[Eye Tracker PC]

    ga --->|"Channel 3 (CO<sub>2</sub>)"| biopac3
    ga --->|"Channel 4 (O<sub>2</sub>)"| biopac3

    sb --->|USB| pc1["Stimuli presentation Laptop ({{ secrets.hosts.psychopy | default("███") }})"]
    pc1 <--->|Ethernet| et
    pc1 --->|USB| modem[MMBT-S Interface]
    pc1 --->|HDMI| display[Projector]
    modem --->|25-pin parallel| biopac4

    BIOPAC --->|Ethernet| pc2["Physio-recording Laptop ({{ secrets.hosts.acqknowledge | default("███") }})"]
```

The above graph can be broken down as follows:

1. **Signals generating from the Scanning Room**.
    In addition to the MR imagery produced by the scanner, the participant will be wearing the RB, the ECG leads, a nasal cannula to retrieve the expired gases, and finally their right eye will be recorded with the infrared camera of the ET.
    All those probes and devices carry signals outside the Scanning Room either through cables or tubes, with the access panel as the interface.
    The access panel also has a connector carrying the **trigger** signals generated by MR schemes, which indicate important events in the MRI acquisition (typically, one trigger pulse is generated for each new *repetition time* —TR—, e.g., with every fMRI volume).
1. **Syncbox**.
    A *NordicLabs Syncbox* receives TTL (transistor-transistor logic) triggers from the scanner.
    This box can just forward the triggers converted into other formats and/or manipulate them (e.g., filter, generate, etc.).
1. **Gas analyzer (GA)**.
    The GA is a device that continuously measures the amount of two gases (CO<sub>2</sub> and O<sub>2</sub>) from a sample fed at the front of the device with a connected tube (this tube comes from inside of the Scanning Room and ends in the nasal cannula the participant is wearing, as shown in the graph).
1. **BIOPAC**.
    The BIOPAC is the main recording hub.
    It directly receives analogical signals from the Scanning Room (for the case of the RB and the ECG).
    Indirectly, it receives the analogical signal from the GA, and digital signals from the *Psychopy laptop* ({{ secrets.hosts.psychopy | default("███") }}).

1. **Eye tracker (ET)**.
    The ET is composed of two main elements:
    (i) inside the scanner's bore, we place an arm that holds an infrared lens and camera sensor on one side and an infrared lamp that illuminates the right eye of the subject through a special mirror to reflect the infrared spectrum; and (ii) a PC tower that receives the camera recordings, postprocess the images and calculates the final parameters of interest (position of the eye, pupil size, etc.).
    The ET is also connected to the *Psychopy laptop* ({{ secrets.hosts.psychopy | default("███") }}), and communicates bi-directionally with it (e.g., to record logs or receive "messages" such as triggers or task events).
    The ET **is NOT connected to the BIOPAC**, with the implication that the ET data is not stored with the other physiological information.
1. **Stimuli presentation laptop**.
    The *Psychopy laptop* ({{ secrets.hosts.psychopy | default("███") }}) has the *Psychopy* software install and with it, the task programs are executed.
    This experiment consists of three tasks: breath-holding task (*BHT*), resting-state fMRI (*rest*), and a positive-control task (*PCT*).
    This laptop also stores the data recorded by the ET at the end of the experiment.
1. **Physiology recording laptop**.
    The *AcqKnowledge laptop* ({{ secrets.hosts.acqknowledge | default("███") }}) runs the BIOPAC's *AcqKnowledge* software and with it, this computer records the signals and allows visualization of the data coming from the BIOPAC.
    All the inputs to the BIOPAC are multiplexed through into an Ethernet cable that is connected to this laptop.

## Getting familiar with the instruments

!!! warning "Emergency procedures"

    It is critical you fully understand and study the [emergency procedures to run an MRI scan at CHUV](./emergency-procedures.md).

??? abstract "BIOPAC documentation and devices"

    Get familiar with the BIOPAC setup and read through the [hardware documentation](https://www.biopac.com/wp-content/uploads/MP_Hardware_Guide.pdf).
    The system is composed by the main unit (MP160; extreme left in the picture below), to which modules are attached depending on what signals are to be recorded.

    ![Biopac_setup](../assets/images/Biopac_setup.jpg "BIOPAC front side")

    Additional modules in our settings are (from left to right in the above picture):

    * The [SPT100D (solid state relay driver unit)](../assets/files/STP100D.pdf) is used to input digital signals that must be recorded.
    * The AMI100C unit can receive up to 16 analog signals.
    * The DA100C unit records the signal coming from the respiration belt.
        This unit requires the pressure transducer TSD160A unit to be connected.
    * The ECG100C MRI unit records the electrical signal coming from the heart via the ECG.
        This unit requires the MECMRI-2 unit on the ECG100C unit.

    In addition to the main unit, we have a data modem to feed digital signals into the SPT100D.
    This modem is the [MMBT-S Trigger Interface Box adapter (N-shaped, pink box)](../assets/files/MMBT-S_instruction_manual_v2.2.pdf):

    ![neurospec](../assets/images/neurospec.jpg)

??? abstract "AD Instrments ML206 Gas analyzer: documentation and basics"

    The front of the gas analyzer (GA) looks like this:

    ![gaz-analyser-front](../assets/images/gaz-analyser-front.jpg "Gas Analyzer front")

    It is critical to familiarize with the [GA's manual](../assets/files/GA_manual.pdf) to learn about its correct utilization.

    !!! danger "Make sure you understand the switching on and off procedures described in these SOPs"

    The input to the GA must be connected through a DM-060-24 dessicant chamber and a MLA0110 flow valve and a MLA0343 drying tube:

    ![desiccant_chamber](../assets/images/desiccant_chamber.png)

    !!! warning "The MLA0343 drying tube and the DM-060-24 dessicant chamber MUST be replaced when their inside color turns into pink."

    Finally, make sure to watch the following video:
    <video id="wistia_simple_video_119" crossorigin="anonymous" style="background: transparent; display: block; height: 100%; max-height: none; max-width: none; position: static; visibility: visible; width: 100%; object-fit: fill;" aria-label="Video" src="https://embed-ssl.wistia.com/deliveries/5e08ccab25ab45382329671a82dfe5123f6e840e/file.mp4" playsinline="" preload="metadata" type="video/mp4" x-webkit-airplay="allow" controls>
      <source src="https://embed-ssl.wistia.com/deliveries/5e08ccab25ab45382329671a82dfe5123f6e840e/file.mp4" type="video/mp4" />
      Your browser does not support the video. <a href="./assets/files/GA_video.mp4">Click here to download it</a>
    </video>