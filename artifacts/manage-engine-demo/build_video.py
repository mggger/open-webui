"""Build the English tutorial from original screenshots (Pillow + FFmpeg)."""
from pathlib import Path
import subprocess, shutil
from PIL import Image, ImageDraw, ImageFont, ImageOps
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'video'; OUT.mkdir(exist_ok=True)
FONT = '/System/Library/Fonts/Supplemental/Arial.ttf'
BOLD = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
# Crops use half-resolution screenshot coordinates; originals remain untouched.
STEPS = [
('01-enable-menu', 'Enable Manage Engine', (680,465,1270,790),10,
 'Click the + button in a new chat, then turn on Manage Engine.\nYour administrator must configure the API connection first.'),
('02-enabled-search-prompt', 'Ask for tickets in plain English', (680,465,1270,640),12,
 'Check that the Manage Engine badge is visible, then send your question.\nInclude a person, ticket status and time range, such as open tickets this week.'),
('03-search-results', 'Read the results and search scope', (635,130,1320,575),12,
 'Review the matching tickets, priorities, statuses and ServiceDesk links.\nCheck scope warnings: this search scanned only the newest 1,000 tickets.'),
('04-missing-description', 'Create a ticket and fill required fields', (635,390,1320,745),12,
 'Ask to create a ticket with a subject, description and requester.\nIf required information is missing, provide it in your next message.'),
('05-review-before-confirm', 'Review first, then confirm', (635,300,1320,755),14,
 'Review the completed draft, then send its exact Confirm create command.\nUse the current draft ID; send Cancel create with that ID to cancel instead.'),
('06-ticket-created', 'Keep the new ticket ID', (635,320,1320,745),10,
 'After confirmation, check the creation result and save the ticket ID.\nThis demonstration created ticket 10510; use the link to open it in ServiceDesk.'),
('07-ticket-details', 'Look up a ticket by ID', (635,350,1320,740),10,
 'Ask: "Show the details of ticket 10510." Use your own ticket ID.\nReview its description, requester, assigned technician, priority and status.'),
]
def font(n,bold=False): return ImageFont.truetype(BOLD if bold else FONT,n)
def stamp(sec):
 h,rem=divmod(sec,3600);m,s=divmod(rem,60);return f'{h:02}:{m:02}:{s:02},000'
srt=[];t=0;segments=[]
for i,(name,title,crop,duration,caption) in enumerate(STEPS,1):
 canvas=Image.new('RGB',(1920,1080),'#0c1629'); d=ImageDraw.Draw(canvas)
 d.text((72,32),'OPEN WEBUI  /  MANAGE ENGINE',font=font(22,True),fill='#64dbc4')
 d.text((72,71),title,font=font(40,True),fill='white')
 d.text((1710,73),f'{i:02} / 07',font=font(30),fill='#b2c2d6')
 d.rounded_rectangle((65,142,1855,898),radius=22,fill='#ffffff')
 screenshot=Image.open(ROOT/(name+'.png')).convert('RGB').crop(tuple(v*2 for v in crop))
 screenshot=ImageOps.contain(screenshot,(1740,726),Image.Resampling.LANCZOS)
 canvas.paste(screenshot,((1920-screenshot.width)//2,156+(726-screenshot.height)//2))
 for j,line in enumerate(caption.splitlines()):
  f=font(31);width=d.textlength(line,font=f)
  assert width<1810,(name,width)
  d.text(((1920-width)/2,930+j*45),line,font=f,fill='white')
 for j in range(7):
  x=72+j*255;d.rounded_rectangle((x,1040,x+235,1045),radius=2,fill='#64dbc4' if j<i else '#29374c')
 frame=OUT/f'{i:02}-captioned.png';canvas.save(frame)
 segment=OUT/f'{i:02}.mp4';segments.append(segment)
 subprocess.run(['ffmpeg','-y','-loglevel','error','-loop','1','-i',str(frame),'-t',str(duration),'-r','24','-c:v','libx264','-preset','fast','-tune','stillimage','-crf','18','-pix_fmt','yuv420p',str(segment)],check=True)
 srt.append(f'{i}\n{stamp(t)} --> {stamp(t+duration)}\n{caption}\n');t+=duration
 print(f'Rendered {i}/7: {title}',flush=True)
(ROOT/'manage-engine-tutorial.en.srt').write_text('\n'.join(srt))
concat=OUT/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in segments))
subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(ROOT/'manage-engine-tutorial.mp4')],check=True)
print(f'Finished: {t} seconds, 1920x1080, 24 fps',flush=True)
