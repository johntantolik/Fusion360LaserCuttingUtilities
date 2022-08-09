#Author-John Antolik
#Description-detect all flat bodies in the current selection and save to a single dxf file for laser cutting

from tkinter import OUTSIDE
import adsk.core, adsk.fusion, traceback

handlers = []

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui: adsk.core.UserInterface = app.userInterface

        # get the command definitions
        cmdDefs = ui.commandDefinitions

        # create a command definition
        cmdDef = cmdDefs.addButtonDefinition('LaserExportButtonId', 'Laser Cut', 
        'Checks if selected bodies can be laser cut and outputs selection to a single DXF file if so.', './resources')

        # connect to command created event
        laserExportCommandCreated = laserExportCommandCreatedEventHandler()
        cmdDef.commandCreated.add(laserExportCommandCreated)
        handlers.append(laserExportCommandCreated)

        # add the button to requisite control panels (next to '3D Print' command)
        qat = ui.toolbars.itemById('QAT')
        fileDropDown = qat.controls.itemById('FileSubMenuCommand')
        fileDropDown.controls.addCommand(cmdDef, 'ThreeDprintCmdDef', True)
        makePanel: adsk.core.ToolbarPanel = ui.allToolbarPanels.itemById('SolidMakePanel')
        cmdControl = makePanel.controls.addCommand(cmdDef, 'ThreeDprintCmdDef', False)
        cmdControl.isPromotedByDefault = True
        cmdControl.isPromoted = True
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


# event handler for commandCreated event
class laserExportCommandCreatedEventHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self) -> None:
        super().__init__()
    
    def notify(self, args):
        eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
        cmd = eventArgs.command

        app = adsk.core.Application.get()
        des = adsk.fusion.Design.cast(app.activeProduct)
        unitsManager = des.unitsManager
        units = unitsManager.defaultLengthUnits

        # build the ui with command inputs
        inputs: adsk.core.CommandInputs = cmd.commandInputs

        selectionInput = inputs.addSelectionInput('selection', 'Selection', 'Select bodies to laser cut')
        selectionInput.addSelectionFilter('SolidBodies')
        selectionInput.setSelectionLimits(0, 0)

        kerfInput = inputs.addValueInput('kerf', 'Kerf', units, adsk.core.ValueInput.createByReal(0.0))

        # connect to the execute event
        onExecute = laserExportCommandExecuteHandler()
        cmd.execute.add(onExecute)
        handlers.append(onExecute)


# event handler for execute event
class laserExportCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self) -> None:
        super().__init__()
    
    def notify(self, args):
        eventArgs = adsk.core.CommandEventArgs.cast(args)
        
        #### code to execute the command ####
        try:
            app = adsk.core.Application.get()
            ui  = app.userInterface
            des = adsk.fusion.Design.cast(app.activeProduct)
            unitsManager = des.unitsManager
            units = unitsManager.defaultLengthUnits
            root: adsk.fusion.Component = adsk.fusion.Component.cast(des.rootComponent)

            # get the selection from the command inputs
            inputs = eventArgs.command.commandInputs
            selectionInput = inputs.itemById('selection')
            kerf = inputs.itemById('kerf').value

            # get the bodies to export from the use5r selection
            bodies = []
            for i in range(selectionInput.selectionCount):
                selectedEntity = selectionInput.selection(i).entity
                if selectedEntity.objectType == adsk.fusion.BRepBody.classType():
                    bodies.append(selectedEntity)

            if len(bodies) == 0:
                return

            # make a sketch to accumulate all of the face profiles
            accumulateSketch: adsk.fusion.Sketch = root.sketches.add(root.xYConstructionPlane)
            accumulateSketch.isComputeDeferred = True  # dont compute the sketch to increase performance
            resultStr = ''
            numFlatBodies = 0
            spacing = 0.5
            xDispTotal = 0.0

            # export each body
            for body in bodies:
                # get all the faces of the body, sorted by area because the profile sides are likely to be largest
                faces = body.faces
                sortedFaces = [face for face in faces]
                sortedFaces.sort(key = lambda f: f.area, reverse = True)

                # check if the body is flat with respect to the largest face
                flat, thickness = isBodyFlat(sortedFaces[0], body)
                if flat:
                    numFlatBodies += 1
                    resultStr += body.name + ' can be cut from ' + str(round(unitsManager.convert(thickness, 
                        'internalUnits', units), 3)) + ' ' + unitsManager.formatUnits(units) + ' material\n'

                    # make a temporary sketch from the face
                    # this automatically projects the face onto the sketch, seemingly even when the option to do so in preferences is turned off
                    tempSketch: adsk.fusion.Sketch = root.sketches.add(sortedFaces[0])
                    tempSketch.isComputeDeferred = True

                    # offset profile to compensate for laser kerf
                    if kerf > 0.0:
                        for profile in tempSketch.profiles:
                            # determine which is the main profile
                            if profile.profileLoops.count > 1:
                                break
                        loops = profile.profileLoops
                        for loop in loops:
                            prCurves = loop.profileCurves
                            skCurves = convertProfCurvesToSketchCurves(prCurves)
                            if loop.isOuter:
                                # expand the loop
                                tempSketch.offset(skCurves, getPointOutsideCurves(skCurves), kerf)
                            else:
                                # contract the loop
                                tempSketch.offset(skCurves, getPointInsideCurves(skCurves), kerf)
                            # delete the original geometry
                            for curve in skCurves:
                                curve.deleteMe() 

                    # move the sketch onto the root XY plane
                    tempSketch.redefine(root.xYConstructionPlane)  

                    # now copy the sketch curves onto the accumulate sketch with the correct displacements
                    xDisp = -tempSketch.boundingBox.minPoint.x + xDispTotal
                    yDisp = -tempSketch.boundingBox.minPoint.y
                    tempSketch.copy(getAllSketchCurves(tempSketch), getXYTranslationMatrix(xDisp, yDisp), accumulateSketch)

                    # update the total size of the sketch
                    width = tempSketch.boundingBox.maxPoint.x - tempSketch.boundingBox.minPoint.x
                    xDispTotal += width + spacing

                    # delete the sketch
                    tempSketch.deleteMe()
                else:
                    resultStr += body.name + ' is not flat\n'

            ui.messageBox('Detected ' + str(numFlatBodies) + ' bodies to export for laser cutting:\n\n' + resultStr)

            if numFlatBodies > 0:
                # get file path from user to save the dxf
                fileDialog = ui.createFileDialog()
                fileDialog.isMultiSelectEnabled = False
                fileDialog.title = "Specify file to save DXF"
                fileDialog.filter = 'DXF files (*.dxf)'
                fileDialog.filterIndex = 0
                dialogResult = fileDialog.showSave()

                # save the profiles to dxf
                if dialogResult == adsk.core.DialogResults.DialogOK:
                    filename = fileDialog.filename
                    accumulateSketch.saveAsDXF(filename)

            # clean up the sketch
            accumulateSketch.deleteMe()
        except:
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        

# clean up the added buttons when the add-in is stopped
def stop(context):
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        # clean up the command
        cmdDef = ui.commandDefinitions.itemById('LaserExportButtonId')
        if cmdDef:
            cmdDef.deleteMe()

        # clean up the buttons
        qat = ui.toolbars.itemById('QAT')
        fileDropDown = qat.controls.itemById('FileSubMenuCommand')
        cntrl = fileDropDown.controls.itemById('LaserExportButtonId')
        if cntrl:
            cntrl.deleteMe()

        makePanel = ui.allToolbarPanels.itemById('SolidMakePanel')
        cntrl = makePanel.controls.itemById('LaserExportButtonId')
        if cntrl:
            cntrl.deleteMe()
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def isBodyFlat(face, body, rigorous = False):
    # conditions which must all be satisfied in order for the body to be flat, i.e. can be laser cut:
    # 1) has a flat face
    # 2) has exactly one face encountered by a ray cast normal to the first face
    # 3) this new face must be flat and parallel to the first face
    # 4) all edges in the body that do not belong to one of these faces must be straight lines and normal to the first face

    # since the largest face is the profile to be laser cut 99% of the time, we only need to perform these checks starting with the largest face of the body
    # this will be much faster than performing the check for every face on the body

    result = False
    bodyThickness = 0.0

    if isFacePlanar(face):
        # get the normal and reverse its direction so it points into the body
        normal: adsk.core.Vector3D = getPlanarFaceNormal(face)
        normal.scaleBy(-1.0)
        
        # cast a ray to find the back face of the body
        # first need to get the root component since it only seems to work to cast a ray from the root comp
        app = adsk.core.Application.get()
        des = adsk.fusion.Design.cast(app.activeProduct)
        root: adsk.fusion.Component = adsk.fusion.Component.cast(des.rootComponent)

        # cast the ray, looking for faces (entityType=1)
        hitCol: adsk.core.ObjectCollection = adsk.core.ObjectCollection.create()
        objCol = root.findBRepUsingRay(face.pointOnFace, normal, 1, 1e-5, False, hitCol)

        # now we need to exclude any faces we found that don't belong to this body
        intersectedFaces = []
        for obj, hit in zip(objCol, hitCol):
            if obj.body == body and obj != face:
                originPoint = hit
                intersectedFaces.append(obj)
                
        if len(intersectedFaces) == 1:
            backFace = intersectedFaces[0]
            # we have intersected exactly one face
            # now check that this face is planar and parallel to the first face
            if isFacePlanar(backFace) and face.geometry.isParallelToPlane(backFace.geometry):
                if rigorous:
                    # finally, we need to check that all of the edges in the body that don't belong to these faces are lines and perpendicular to them
                    # this part is quite slow
                    for edge in body.edges:
                        if edge not in face.edges and edge not in backFace.edges:
                            if edge.geometry.curveType != 0 or not face.geometry.isPerpendicularToLine(edge.geometry):
                                break
                    else:
                        # we get here if the for loop completes without breaking
                        result = True
                else:
                    # if we don't need to rigorously determine if the body can be laser cut, just check that the face areas are the same as a quick litmus test
                    if abs(face.area - backFace.area) < 1e-4:
                        result = True

    # calculate the material thickness needed
    if result:
        bodyThickness = originPoint.distanceTo(face.pointOnFace)

    return result, bodyThickness


def isFacePlanar(face) -> bool:
    # surfaceType is an enum, value of 0 indicates plane
    return face.geometry.surfaceType == 0


def getPlanarFaceNormal(face) -> adsk.core.Vector3D:
    # use a surfaceEvaluator so the normal is guaranteed to point outward from the body
    success, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    if success:
        return normal
    else:
        return adsk.core.Vector3D.create() # return a default vector if fails


def getIndicatorAppearance() -> adsk.core.Appearance:
    app: adsk.fusion.Application = adsk.core.Application.get()
    des: adsk.fusion.Design = app.activeProduct

    appearanceName = 'laserCutScriptIndicator'
    indAppearance = des.appearances.itemByName(appearanceName)

    if indAppearance is None:
        # get a base appearance from the material library
        lib = app.materialLibraries.itemByName('Fusion 360 Appearance Library')
        baseAppearance: adsk.core.Appearance = lib.appearances.itemByName('Plastic - Matte (Yellow)')
        indAppearance: adsk.core.Appearance = des.appearances.addByCopy(baseAppearance, appearanceName)

    return indAppearance


def getAllSketchCurves(sketch) -> adsk.core.ObjectCollection:
    sketchEntities: adsk.core.ObjectCollection = adsk.core.ObjectCollection.create()
    [sketchEntities.add(curve) for curve in sketch.sketchCurves]  # add all of the curves in the sketch to a collection
    return sketchEntities


def getXYTranslationMatrix(xDisp, yDisp) -> adsk.core.Matrix3D:
    matMove: adsk.core.Matrix3D = adsk.core.Matrix3D.create()
    translation: adsk.core.Vector3D = adsk.core.Vector3D.create()
    translation.x = xDisp
    translation.y = yDisp
    matMove.translation = translation
    return matMove


def convertProfCurvesToSketchCurves(prCurves) -> adsk.core.ObjectCollection:
    skCurves = adsk.core.ObjectCollection.create()
    for curve in prCurves:
        skCurves.add(curve.sketchEntity)
    return skCurves


def getPointOutsideCurves(skCurves) -> adsk.core.Point3D:
    # construct the bounding box for the curves
    curve = adsk.fusion.SketchCurve.cast(None)
    pointSets = []
    boundBox = None
    for curve in skCurves:
        if not boundBox:
            boundBox = curve.boundingBox
        else:
            boundBox.combine(curve.boundingBox)

    # move a point outside the bouding box
    cornerVec = boundBox.minPoint.vectorTo(boundBox.maxPoint)
    cornerVec.normalize() 
    outsidePoint = boundBox.maxPoint.copy()
    outsidePoint.translateBy(cornerVec)
    return outsidePoint


# from:
# https://forums.autodesk.com/t5/fusion-360-api-and-scripts/how-to-determine-a-directionpoint-to-offset-an-arbitrary-sketch/m-p/6425999#M1930
def getPointInsideCurves(skCurves) -> adsk.core.Point3D:
    try:
        # Build up list of connected points.
        curve = adsk.fusion.SketchCurve.cast(None)
        pointSets = []
        boundBox = None
        for curve in skCurves:
            if not boundBox:
                boundBox = curve.boundingBox
            else:
                boundBox.combine(curve.boundingBox)            
                
            if isinstance(curve, adsk.fusion.SketchLine):
                skLine = adsk.fusion.SketchLine.cast(curve)
                pointSets.append((skLine.startSketchPoint.geometry, skLine.endSketchPoint.geometry))
            else:
                curveEval = adsk.core.CurveEvaluator3D.cast(curve.geometry.evaluator)
                (retVal, startParam, endParam) = curveEval.getParameterExtents()
                
                (retVal, strokePoints) = curveEval.getStrokes(startParam, endParam, 0.1)
                pointSets.append(strokePoints)
            
        # Create two points that define a line that crosses the entire range.  They're moved
        # to be outside the bounding box so there's not problem with coincident points.
        lineVec = boundBox.minPoint.vectorTo(boundBox.maxPoint)
        lineVec.normalize()
        maxPoint = boundBox.maxPoint.copy()
        maxPoint.translateBy(lineVec)
        lineVec.scaleBy(-1)
        minPoint = boundBox.minPoint.copy()
        minPoint.translateBy(lineVec)
    
        # Iterate through all of the lines and get the intersection between the lines
        # and the long crossing line.
        intPointList = []
        for pointSet in pointSets:
            pnt1 = None
            pnt2 = None
            for point in pointSet:
                if not pnt2:
                    pnt2 = point
                else:
                    pnt1 = pnt2
                    pnt2 = point
    
                    intCoords = getLineIntersection(minPoint.x, minPoint.y, maxPoint.x, maxPoint.y, pnt1.x, pnt1.y, pnt2.x, pnt2.y)
                    if intCoords:
                        intPoint = adsk.core.Point3D.create(intCoords[0], intCoords[1], 0)
                        intPointList.append((intPoint, intPoint.distanceTo(minPoint)))
        
        # Make sure at last two intersection points were found.
        if len(intPointList) >= 2:
            # Sort the points by the distance from the min point.
            sortedPoints = sorted(intPointList, key=getKey) 
        
            # Get the first two points and compute a mid point.  That's a point in the area.                
            pnt1 = sortedPoints[0]
            pnt2 = sortedPoints[1]
            midPoint = adsk.core.Point3D.create((pnt1[0].x + pnt2[0].x)/2, (pnt1[0].y + pnt2[0].y)/2, 0)
            return midPoint
        else:
            return False
    except:
        ui = adsk.core.Application.get().userInterface
        ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
    
                                
# Function that's used to get the value to sort with.
def getKey(item):
    return item[1]

                
# Calculate the intersection between two line segments.  This is based on the JS
# sample in the second answer at:
# http://stackoverflow.com/questions/563198/how-do-you-detect-where-two-line-segments-intersect
def getLineIntersection(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y):
    s1_x = p1_x - p0_x
    s1_y = p1_y - p0_y
    s2_x = p3_x - p2_x
    s2_y = p3_y - p2_y

    s = (-s1_y * (p0_x - p2_x) + s1_x * (p0_y - p2_y)) / (-s2_x * s1_y + s1_x * s2_y)
    t = ( s2_x * (p0_y - p2_y) - s2_y * (p0_x - p2_x)) / (-s2_x * s1_y + s1_x * s2_y)

    if (s >= 0 and s <= 1 and t >= 0 and t <= 1):
        # Collision detected
        i_x = p0_x + (t * s1_x)
        i_y = p0_y + (t * s1_y)
        return (i_x, i_y)

    return False  # No collision