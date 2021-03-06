"""
Checklist for a Gene-Tango run:
GENE parameters file
    diagdir
    read_checkpoint [and copy and rename a start file to checkpoint_000]
    parallelization / number of processors

submit.cmd file
    time limit
    number of processors

Tango python file
    maximum number of iterations
    EWMA parameters
    diagdir
    GENE simulation time per iteration
    density profile
    user_control_func
    etc.   
    
Rule of thumb for theta parameters (which determine the diffusive/convective split) in LoDestro Method:
    --for field U.  set dpdxthreshold = Geometric_Avg[U] * 4e4    (where the geometric average is over the estimated min and max values)
    --Dmin = 1e-5, Dmax = 1e3?  
    --these aren't heavily tested and may well need to be modified.
    --probably a logarithmic derivative should be used for thresholding, but has not been implemented yet
"""

from __future__ import division, absolute_import
import numpy as np
import time

# Tango imports... should simplify these so I'm not importing them all separately!
import tango
import tango.gene_startup
import tango.smoother
import tango.genecomm_unitconversion
import tango.tango_logging as tlog
import tango.utilities.util # for duration_as_hms
import gene_tango

# constants
MAXITERS = 50
DIAGDIR = '/scratch2/scratchdirs/jbparker/genedata/prob##/'
SIMTIME = 50 # GENE simulation time per iteration, in Lref/cref

def initialize_iteration_parameters():
    maxIterations = MAXITERS
    thetaParams = {'Dmin': 1e-5,
                   'Dmax': 1e3,
                   'dpdxThreshold': 400000}
    EWMAParamTurbFlux = 0.3
    EWMAParamProfile = 0.3
    lmParams = {'EWMAParamTurbFlux': EWMAParamTurbFlux,
            'EWMAParamProfile': EWMAParamProfile,
            'thetaParams': thetaParams}
    tol = 1e-11  # tol for convergence... reached when a certain error < tol
    return (maxIterations, lmParams, tol)

def density_profile(rho):
    """density profile, fixed in time.
    Inputs:
      rho       normalized radial coordinate rho=r/a (array)
    Outputs:
      T         density profile in SI (array)
    """
    minorRadius = 0.594  # a
    majorRadius = 1.65  # R0
    inverseAspectRatio = minorRadius / majorRadius
    rho0 = 0.5
    
    # density profile
    n0 = 3.3e19;     # in SI, m^-3
    kappa_n = 2.22;  # R0 / Ln    
    deltar = 0.5
    rhominus = rho - rho0 + deltar/2
    deltan = 0.1
    n = n0 * np.exp( -kappa_n * inverseAspectRatio * (rho - rho0 - deltan * (np.tanh(rhominus/deltan) - np.tanh(deltar/2/deltan))))
    
    # set n to a constant for rho < rho0-deltar/2
    ind = int(np.abs(rho - (rho0 - deltar/2)).argmin())
    ind2 = (rho < (rho0-deltar/2))
    n[ind2] = n[ind];
    return n
    
def temperature_initial_condition(rho):
    """Initial temperature profile
    Inputs:
      rho       normalized radial coordinate rho=r/a (array)
    Outputs:
      T         temperature profile in SI (array)
    """
    e = 1.60217662e-19          # electron charge
    
    kappa_T = 6.96
    deltar = 0.9
    rho0 = 0.5
    rhominus = rho - rho0 + deltar/2
    deltaT = 0.1

    T0 = 1000*e
    invasp = 0.36
    T = T0 * np.exp( -kappa_T * invasp * (rho - rho0 - deltaT * (np.tanh(rhominus/deltaT) - np.tanh(deltar/2/deltaT))));
    ind = int(np.abs(rho - (rho0 - deltar/2)).argmin())
    ind2 = (rho < (rho0-deltar/2));
    T[ind2] = T[ind];
    return T
    
    
def source_fhat(rho):
    """Provide the function fhat in the source term V'S = A fhat(rho)
    
    Inputs:
      rho           radial coordinate rho=r/a (array)
    Outputs:
      fhat          output (array)
    """
    rho_a = 0.2
    rho_b = 0.4
    rho0 = 0.3
    w = 0.05
    
    fhat = np.zeros_like(rho)
    ind = (rho > rho_a) & (rho < rho_b)
    fhat[ind] = np.exp( -(rho[ind] - rho0)**2 / w**2)
    return fhat

def source_H7(r, minorRadius, majorRadius, A):
    """Provide the source term V'S to the transport equation.  The contribution to H7 is V'S.
    
    The source is written as V'S = A * f(r) = A * fhat(r/a)
    
    Inputs:
      r             radial coordinate r, in m (array)
      minorRadius   minor radius a, in m (scalar)
      majorRadius   major radius R0, in m (scalar)
      A             amplitude in SI of V'S, in SI (scalar)
    Outputs:
      H7contrib     contribution to H7 (=V'S) (array)
    """
    rho = r / minorRadius
    fhat = source_fhat(rho)
    H7contrib = A * fhat
    return H7contrib
        
def read_seed_turb_flux():
    """Read in a file that contains a turbulent heat flux profile to use as the EWMA seed.
    
    The seed should probably come from a long run of GENE (long compared to a single iteration here) so
    that the heat flux is averaged over many cycles.  Heat flux here is qhat, not V' qhat."""
    filenameHeatFluxSeed = 'heat_flux_seed'
    heatFluxProfile = np.loadtxt(filenameHeatFluxSeed, unpack=True)
    return heatFluxProfile
    
class ComputeAllH(object):
    def __init__(self, Vprime, minorRadius, majorRadius, A):
        self.Vprime = Vprime
        self.minorRadius = minorRadius
        self.majorRadius = majorRadius
        self.A = A
    def __call__(self, t, r, profiles, HCoeffsTurb):
        """Define the contributions to the H coefficients
        
        Inputs:
          t         time (scalar)
          r         radial coordinate in SI (array)
          pressure  pressure profile in SI (array)
        """
        # pressure = profiles['pi']
        H1 = 1.5 * self.Vprime
        
        # add some manually input diffusivity
        D_adhoc = 0.05  # in SI, m^2/s
        H2_adhoc = D_adhoc * self.Vprime
        

        # Heat Source
        H7 = source_H7(r, self.minorRadius, self.majorRadius, self.A)
        
        HCoeffs = tango.multifield.HCoefficients(H1=H1, H2=H2_adhoc, H7=H7)
        HCoeffs = HCoeffs + HCoeffsTurb
        return HCoeffs
        
def problem_setup():
    """
    Still to be done: need to choose good profiles for
        ion temperature initial condition
        plasma density
        heat source
        tango and GENE radial grids
    """
    # Set up problem parameters
    minorRadius = 0.594     # minor radius a, in meters
    majorRadius = 1.65      # major radius R0, in meters
    
    # Set up the turbulence (GENE) grid
    rhoLeftBndyGene = 0.1
    rhoRightBndy = 0.9
    numRadialPtsGene = 120  # from parameters file... needs to be consistent
    rhoGene = np.linspace(rhoLeftBndyGene, rhoRightBndy, numRadialPtsGene)
    rGene = rhoGene * minorRadius
    drGene = rGene[1] - rGene[0]
    
    # Set up the transport (Tango) grid
    numRadialPtsTango = 27
    drho = rhoRightBndy / (numRadialPtsTango - 0.5)  # spatial grid size
    rhoTango = np.linspace(drho/2, rhoRightBndy, numRadialPtsTango)  # Tango inner-most point is set at delta rho/2, not exactly zero.
    rTango = rhoTango * minorRadius # physical radius r, measured in meters, used as the independent coordinate in Tango
    # drTango = rTango[1] - rTango[0]
    L = rTango[-1]  # size of domain
    
    #VprimeGene = 4 * np.pi**2 * majorRadius * rGene
    VprimeTango = 4 * np.pi**2 * majorRadius * rTango
    #gradPsiSqTango = np.ones_like(rTango) # |grad r|^2 = 1
    
    Bref = 1.14
    B0 = Bref
    Lref = 1.65
    Tref = 1
    nref = 3.3
    ionMass = 2  # in proton masses
    mref = ionMass
    ionCharge = 1
    #mref = 2  # do I need mref??
    
    densityProfileTango = density_profile(rhoTango)
    
    # create object for interfacing tango and GENE radial grids
       # must be consistent with whether Tango's or Gene's radial domain extends farther radially outward
    rExtrapZoneLeft = 0.75 * minorRadius
    rExtrapZoneRight = 0.80 * minorRadius
    polynomialDegree = 1
    gridMapper = tango.interfacegrids_gene.TangoOutsideExtrapCoeffs(rTango, rGene, rExtrapZoneLeft, rExtrapZoneRight, polynomialDegree)
    
    
    # specify a boundary condition for pressure at the outward radial boundary
    temperatureRightBCInkeV = 0.44
    e = 1.60217662e-19          # electron charge
    temperatureRightBC = temperatureRightBCInkeV * 1000 * e  # temperature in SI
    pressureRightBC = temperatureRightBC * densityProfileTango[-1]
    
    # specify a temperature and pressure initial condition
    temperatureICTango = temperature_initial_condition(rhoTango) # in SI
    pressureICTango = temperatureICTango * densityProfileTango
    
    # specify safety factor
    safetyFactorGeneGrid = 0.868 + 2.2 * rhoGene**2
    
    # GENE setup
    fromCheckpoint = True    # true if restarting a simulation from an already-saved checkpoint
    geneFluxModel = tango.gene_startup.setup_gene_run_singleion_adiabaticelectrons(
            rTango, rGene, minorRadius, majorRadius, B0, ionMass, ionCharge, densityProfileTango, pressureICTango, safetyFactorGeneGrid,
            Bref, Lref, Tref, nref, gridMapper, fromCheckpoint)
    
    
    # other transport physics / physicsToH object creation
    #profilesAll = tango.physics.initialize_profile_defaults(ionMass, densityProfile, psiTango, minorRadius, majorRadius, B0, Vprime, gradPsiSq)
    #HcontribTransportPhysics = tango.physics_to_H.Hcontrib_TransportPhysics(profilesAll)
    
    # iteration parameters setup
    (maxIterations, lmParams, tol) = initialize_iteration_parameters()    
    
    # create flux smoother for spatial averaging of flux
    windowSizeInGyroradii = 10
    rhoref = tango.genecomm_unitconversion.rho_ref(Tref, mref, Bref)
    windowSizeInPoints = int( np.round(windowSizeInGyroradii * rhoref / drGene) )
    fluxSmoother = tango.smoother.Smoother(windowSizeInPoints)
    
    # specify a source function amplitude
    A = 57.3e6 # in SI, W/m^3  # for total input power of 3 MW
    
    tArray = np.array([0, 1e4])  # specify the timesteps to be used.
    
    # creation of turbulence handler
    turbHandler = tango.lodestro_method.TurbulenceHandler(drGene, rTango, geneFluxModel, VprimeTango=VprimeTango, fluxSmoother=fluxSmoother)
    
    compute_all_H_pressure = ComputeAllH(VprimeTango, minorRadius, majorRadius, A)
    lodestroMethod = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    
    # seed the EWMA D,c for the turbulent heat flux
    heatFluxSeed = read_seed_turb_flux()
    smoothedFluxTurbGrid = fluxSmoother.smooth(heatFluxSeed)
    pressureTurbGrid = gridMapper.map_profile_onto_turb_grid(pressureICTango)
    #   convert flux to D,c
    (DTurbGrid, cTurbGrid, _) = lodestroMethod.flux_to_transport_coeffs(smoothedFluxTurbGrid, pressureTurbGrid, drGene)
    lodestroMethod.set_ewma_turb_D(DTurbGrid)
    lodestroMethod.set_ewma_turb_c(cTurbGrid)
    
    field0 = tango.multifield.Field(
                label='pi', rightBC=pressureRightBC, profile_mminus1=pressureICTango, compute_all_H=compute_all_H_pressure,
                lodestroMethod=lodestroMethod, gridMapper=gridMapper)
    fields = [field0]
    tango.multifield.check_fields_initialize(fields)
    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
    
    return (L, rTango, rGene, pressureRightBC, pressureICTango, maxIterations, tol, geneFluxModel, turbHandler, compute_all_H_all_fields, tArray, fields)

    
# ************************************************** #
####              START OF MAIN PROGRAM           ####
# ************************************************** #
MPIrank = gene_tango.init_mpi()
tlog.setup(True, MPIrank, tlog.DEBUG)
(L, rTango, rGene, pressureRightBC, pressureICTango, maxIterations, tol, geneFluxModel, turbHandler, compute_all_H_all_fields, tArray, fields) = problem_setup()


# set up FileHandlers
###  GENE output to save periodically.  For list of available, see handlers.py

### save the GENE checkpoint file --- distribution function f1
diagdir = DIAGDIR
f1HistoryHandler = tango.handlers.SaveGeneOutputHandler('checkpoint_000', iterationInterval=10, diagdir=diagdir)

### save the GENE data analysis output
geneFilesToSave = [name + '_000' for name in ['field', 'mom_ions', 'nrg', 'profile_ions', 'profiles_ions', 'srcmom_ions', 'vsp']]
geneOutputHandler = tango.handlers.SaveGeneOutputHandler(*geneFilesToSave, iterationInterval=1, diagdir=diagdir)

### set up Tango History handler
setNumber = 0
t = tArray[1]
initialData = tango.handlers.TangoHistoryHandler.set_up_initialdata(setNumber, rTango, rGene, t, fields)
basename = 'tangodata'   # without restart, saves into tangodata_s0.hdf5
tangoHistoryHandler = tango.handlers.TangoHistoryHandler(iterationInterval=1, basename=basename, maxIterations=maxIterations, initialData=initialData)
filenameTangoHistory = basename + '_s{}'.format(setNumber) + '.hdf5'


#  specify how long GENE runs between Tango iterations.  Specified in Lref/cref
geneFluxModel.set_simulation_time(SIMTIME)

solver = tango.solver.Solver(L, rTango, tArray, maxIterations, tol, compute_all_H_all_fields, fields)

# Add the file handlers
solver.fileHandlerExecutor.set_parallel_environment(parallel=True, MPIrank=MPIrank)
solver.fileHandlerExecutor.add_handler(f1HistoryHandler)
solver.fileHandlerExecutor.add_handler(geneOutputHandler)
solver.fileHandlerExecutor.add_handler(tangoHistoryHandler)
tlog.info("Tango history handler setup, saving to {}.".format(filenameTangoHistory))




tlog.info("Entering main time loop...")

startTime = time.time()
while solver.ok:
     # Implicit time advance: iterate to solve the nonlinear equation!
     solver.take_timestep()

if solver.reachedEnd == True:
    tlog.info("The solution has been reached successfully.")
    tlog.info("Took {} iterations".format(solver.l))
else:
    tlog.info("The solver failed for some reason.")
    tlog.info("The residual at the end is {}".format(solver.errHistoryFinal[-1]))
    
pi = solver.profiles['pi'] # finished solution
tlog.info("The profile at the end is:")

tlog.info("{}".format(pi))

endTime = time.time()
durationHMS = tango.utilities.util.duration_as_hms(endTime - startTime)
tlog.info("Total wall-time spent for Tango run: {}".format(durationHMS))

gene_tango.finalize_mpi()
