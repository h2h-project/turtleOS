#src/ui/toggle.py

class ToggleSwitch:
    def __init__(self,x,y,w,h):
        self.x=x
        self.y=y
        self.w=w
        self.h=h

    def _circle(self,fb,cx,cy,r,color=1,fill=False):
        x=r
        y=0
        err=0
        while x>=y:
            if fill:
                fb.hline(cx-x,cy+y,2*x+1,color)
                fb.hline(cx-x,cy-y,2*x+1,color)
                fb.hline(cx-y,cy+x,2*y+1,color)
                fb.hline(cx-y,cy-x,2*y+1,color)
            else:
                fb.pixel(cx+x,cy+y,color)
                fb.pixel(cx+y,cy+x,color)
                fb.pixel(cx-y,cy+x,color)
                fb.pixel(cx-x,cy+y,color)
                fb.pixel(cx-x,cy-y,color)
                fb.pixel(cx-y,cy-x,color)
                fb.pixel(cx+y,cy-x,color)
                fb.pixel(cx+x,cy-y,color)
            y+=1
            if err<=0:
                err+=2*y+1
            if err>0:
                x-=1
                err-=2*x+1

    def _pill_filled(self,fb,x,y,w,h,color):
        r=w//2
        if r<2:
            r=2
        cx=x+(w//2)
        top=y+r
        bot=y+h-1-r
        fb.fill_rect(x,top,w,bot-top+1,color)
        self._circle(fb,cx,top,r,color,fill=True)
        self._circle(fb,cx,bot,r,color,fill=True)

    def draw(self,fb,on=False,pos=None):
        #pos overrides on= for three-position switches:
        #  0 = hollow knob at bottom (off)
        #  1 = filled knob at middle
        #  2 = filled knob at top (on)
        #pos=None keeps the original two-position behaviour from on=.
        x=self.x;y=self.y;w=self.w;h=self.h

        #outer pill filled
        self._pill_filled(fb,x,y,w,h,1)

        #inner pill cleared to make a border
        inset=2
        if w>8 and h>8:
            self._pill_filled(fb,x+inset,y+inset,w-2*inset,h-2*inset,0)

        #knob
        r=(w//2)-4
        if r<2:
            r=2

        cx=x+(w//2)
        top=y+(w//2)
        bot=y+h-1-(w//2)
        mid=(top+bot)//2

        if pos is None:
            pos=2 if on else 0

        if pos>=2:
            self._circle(fb,cx,top,r,1,fill=True)
        elif pos==1:
            self._circle(fb,cx,mid,r,1,fill=True)
        else:
            self._circle(fb,cx,bot,r,1,fill=False)
